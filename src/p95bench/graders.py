"""Small grader registry. Execution graders run trusted local benchmark artifacts."""

import csv
import json
import math
import os
import re
import shutil
import signal
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from p95bench.demo import demo_app


@dataclass
class Grade:
    passed: bool
    detail: str

    def to_dict(self):
        return asdict(self)


def numeric(text, params, workdir):
    try:
        value = float(text.strip())
        passed = math.isfinite(value) and math.isclose(
            value, float(params["answer"]), rel_tol=0, abs_tol=params.get("tolerance", 0))
        return Grade(passed, "numeric comparison")
    except ValueError:
        return Grade(False, "Expected only a number")


def choice(text, params, workdir):
    return Grade(text.strip().upper() == params["answer"].upper(), "exact choice comparison")


def extract_jmx(text):
    match = re.search(r"<jmeterTestPlan\b[\s\S]*?</jmeterTestPlan>", text)
    if not match:
        raise ValueError("No complete jmeterTestPlan found")
    xml = match.group()
    ET.fromstring(xml)
    return xml


def run_jmeter(command, log, timeout):
    environment = {k: v for k, v in os.environ.items()
                   if not any(secret in k.upper() for secret in
                              ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
    with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                          start_new_session=True, env=environment) as process:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        return process


def jmeter_executable():
    home = Path(os.environ.get("JMETER_HOME", "~/apache-jmeter-5.6.3")).expanduser()
    executable = home / "bin" / "jmeter"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"JMeter executable not found or not executable: {executable}; set JMETER_HOME")
    return executable


def jmeter_exec(text, params, workdir):
    executable = jmeter_executable()
    try:
        xml = extract_jmx(text)
    except (ValueError, ET.ParseError) as error:
        return Grade(False, str(error))
    workdir.mkdir(parents=True, exist_ok=True)
    with demo_app() as (url, admin):
        port = url.rsplit(":", 1)[1]
        plan = workdir / "plan.jmx"
        plan.write_text(xml)
        result = workdir / "samples.jtl"
        command = [str(executable), "-n", "-t", str(plan), "-l", str(result),
                   "-j", str(workdir / "jmeter.log"), "-Jhost=127.0.0.1", f"-Jport={port}",
                   "-Jjmeter.save.saveservice.output_format=csv",
                   "-Jjmeter.save.saveservice.print_field_names=true",
                   "-Jjmeter.save.saveservice.url=true",
                   "-Jjmeter.save.saveservice.thread_name=true",
                   "-Jjmeter.save.saveservice.successful=true"]
        try:
            with (workdir / "stdout.log").open("w") as log:
                process = run_jmeter(command, log, params.get("timeout", 60))
            if process.returncode:
                return Grade(False, f"JMeter exit code {process.returncode}")
            with result.open() as stream:
                rows = list(csv.DictReader(stream))
            if not rows or any(row.get("success") != "true" for row in rows):
                return Grade(False, "Empty run or failed samples")
            minimum_threads = params.get("min_checkout_threads", 1)
            if minimum_threads > 1:
                checkout_threads = {row["threadName"] for row in rows
                                    if row.get("threadName")
                                    and urlsplit(row.get("URL", "")).path == "/checkout"}
                if len(checkout_threads) < minimum_threads:
                    return Grade(False, f"Checkout threads: {len(checkout_threads)}/{minimum_threads}")
            request = urllib.request.Request(url + "/internal/stats", headers={"X-Admin-Key": admin})
            with urllib.request.urlopen(request, timeout=5) as response:
                completed = json.load(response)["completions"]
            expected = params.get("completions", 1)
            return Grade(completed == expected, f"Verified completions: {completed}/{expected}")
        except subprocess.TimeoutExpired:
            return Grade(False, "JMeter timed out")
        except (OSError, ValueError) as error:
            return Grade(False, f"Execution failed: {error}")


def k6_executable():
    executable = shutil.which(os.environ.get("K6_PATH", "k6"))
    if not executable:
        raise ValueError("k6 executable not found or not executable; set K6_PATH or install k6 on PATH")
    return Path(executable)


def k6_exec(text, params, workdir):
    match = re.search(r"```(?:javascript|js)[ \t]*\r?\n([\s\S]*?)```", text, re.IGNORECASE)
    if not match or not match[1].strip():
        return Grade(False, "No javascript code block found")
    executable = k6_executable()
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "script.js"
    script.write_text(match[1])
    points = workdir / "points.json"
    with demo_app() as (url, admin):
        command = [str(executable), "run", "--quiet", "--no-usage-report", "-e", f"BASE_URL={url}",
                   "--out", f"json={points}", ("--system-tags=proto,subproto,status,method,url,name,"
                   "group,check,error,error_code,tls_version,scenario,service,expected_response,vu,iter"),
                   str(script)]
        try:
            with (workdir / "stdout.log").open("w") as log:
                process = run_jmeter(command, log, params.get("timeout", 60))
            if process.returncode:
                return Grade(False, f"k6 exit code {process.returncode}")
            requests = []
            with points.open() as stream:
                for line in stream:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise TypeError("Invalid k6 metric record")
                    if record.get("type") == "Point" and record.get("metric") == "http_reqs":
                        tags = record["data"]["tags"]
                        if not isinstance(tags, dict):
                            raise TypeError("Invalid k6 request tags")
                        vu = record["data"].get("metadata", {}).get("vu", tags.get("vu"))
                        tags = {**tags, "vu": vu}
                        requests.append(tags)
            if not requests:
                return Grade(False, "Empty run: no http_reqs points")
            if any(tags.get("expected_response") != "true"
                   or not 0 < int(tags.get("status", 0)) < 400 for tags in requests):
                return Grade(False, "Failed HTTP samples")
            minimum = params.get("min_checkout_vus", 1)
            checkout_vus = {tags["vu"] for tags in requests if tags.get("vu")
                            and urlsplit(tags.get("url", "")).path == "/checkout"}
            if minimum > 1 and len(checkout_vus) < minimum:
                return Grade(False, f"Checkout VUs: {len(checkout_vus)}/{minimum}")
            request = urllib.request.Request(url + "/internal/stats", headers={"X-Admin-Key": admin})
            with urllib.request.urlopen(request, timeout=5) as response:
                completed = json.load(response)["completions"]
            expected = params.get("completions", 1)
            return Grade(completed == expected, f"Verified completions: {completed}/{expected}")
        except subprocess.TimeoutExpired:
            return Grade(False, "k6 timed out")
        except (OSError, ValueError, KeyError, TypeError) as error:
            return Grade(False, f"Execution failed: {error}")


GRADERS = {"numeric": numeric, "choice": choice, "jmeter_exec": jmeter_exec, "k6_exec": k6_exec}


def grade(task, text, workdir):
    return GRADERS[task.grader](text, task.params, workdir)
