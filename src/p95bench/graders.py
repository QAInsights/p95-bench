"""Small grader registry. Execution graders run trusted local benchmark artifacts."""

import csv
import json
import math
import os
import re
import signal
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

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
            request = urllib.request.Request(url + "/internal/stats", headers={"X-Admin-Key": admin})
            with urllib.request.urlopen(request, timeout=5) as response:
                completed = json.load(response)["completions"]
            expected = params.get("completions", 1)
            return Grade(completed == expected, f"Verified completions: {completed}/{expected}")
        except subprocess.TimeoutExpired:
            return Grade(False, "JMeter timed out")
        except (OSError, ValueError) as error:
            return Grade(False, f"Execution failed: {error}")


GRADERS = {"numeric": numeric, "choice": choice, "jmeter_exec": jmeter_exec}


def grade(task, text, workdir):
    return GRADERS[task.grader](text, task.params, workdir)
