"""Native engine evidence tied to independent demo request identities."""

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from p95bench.demo_v2 import Profile
from p95bench.images import tool_image
from p95bench.worker import Resources, WorkerUnavailable, execution_trial


class AuditOverflow(WorkerUnavailable):
    pass

COMPONENTS = {"jmeterTestPlan", "hashTree", "TestPlan", "ThreadGroup", "LoopController",
              "OnceOnlyController", "IfController", "WhileController", "GenericController",
              "TransactionController", "HTTPSamplerProxy", "CookieManager", "HeaderManager",
              "JSONPostProcessor", "RegexExtractor", "ResponseAssertion", "DurationAssertion",
              "ConstantTimer", "ConstantThroughputTimer", "SyncTimer", "CSVDataSet",
              "Arguments", "Argument", "HTTPArgument", "Header", "stringProp", "boolProp",
              "intProp", "longProp", "doubleProp", "collectionProp", "elementProp"}
FUNCTIONS = {"P", "property", "threadNum", "counter", "time", "Random", "RandomString"}


def artifact_source(tool, text):
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 1048576:
        raise ValueError("Expected a nonempty artifact of at most 1 MiB")
    if tool == "k6":
        fenced = re.fullmatch(r"\s*```(?:javascript|js)?\s*\n([\s\S]*?)\n```\s*", text)
        return fenced[1] if fenced else text
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError("XML declarations with external entities are forbidden")
    match = re.search(r"<jmeterTestPlan\b[\s\S]*?</jmeterTestPlan>", text)
    if not match:
        raise ValueError("No complete jmeterTestPlan")
    root = ET.fromstring(match[0])
    for element in root.iter():
        if element.tag not in COMPONENTS or any(key not in {
                "name", "testname", "testclass", "guiclass", "enabled", "version", "properties",
                "jmeter", "elementType"} for key in element.attrib):
            raise ValueError("JMX component or serialization attribute outside the allowed subset")
        if element.get("testclass", element.get("elementType", element.tag)) != element.get("elementType", element.tag):
            raise ValueError("JMX testclass must match its element")
        if element.get("elementType", "Arguments") not in COMPONENTS:
            raise ValueError("JMX property class outside the allowed subset")
        if element.tag == "IfController" and element.findtext(
                "boolProp[@name='IfController.useExpression']") != "true":
            raise ValueError("IfController must use boolean expression mode")
        value = element.text or ""
        if any(function not in FUNCTIONS for function in re.findall(r"\$\{__([\w]+)\(", value)):
            raise ValueError("JMX executable expression outside the allowed subset")
        name = element.get("name", "")
        if name == "TestPlan.user_define_classpath" and value.strip():
            raise ValueError("Custom JVM classpaths are forbidden")
        if name == "HTTPSampler.implementation" and value not in ("", "Java", "HttpClient4"):
            raise ValueError("Custom HTTP implementation is forbidden")
        if name == "CookieManager.implementation" and value not in (
                "", "org.apache.jmeter.protocol.http.control.HC4CookieHandler"):
            raise ValueError("Custom cookie implementation is forbidden")
    return match[0]


def native_command(tool):
    if tool == "k6":
        return ["k6", "run", "--quiet", "--no-usage-report", "--no-summary", "--log-format=json",
                "--log-output=file=/output/http.log", "--http-debug=full",
                "--out=json=/output/points.json",
                "--system-tags=status,method,url,name,vu,iter,scenario", "/fixtures/model.js"]
    return ["java", "-Xmx128m", "-XX:ActiveProcessorCount=1", "-XX:ParallelGCThreads=1",
            "-Djava.awt.headless=true", "-Duser.home=/tmp", "-jar", "/opt/apache-jmeter-5.6.3/bin/ApacheJMeter.jar",
            "-n", "-t", "/fixtures/model.jmx", "-l", "/output/samples.jtl", "-j", "/output/engine.log",
            "-Jhost=127.0.0.1", "-Jport=8000", "-Jjmeter.save.saveservice.output_format=xml",
            "-Jjmeter.save.saveservice.responseHeaders=true",
            "-Jjmeter.save.saveservice.thread_name=true",
            "-Jjmeter.save.saveservice.assertion_results=all"]


@dataclass
class Trial:
    loaded: bool
    status: str
    returncode: int | None
    events: list
    samples: list
    points: list
    started: float = 0


def k6_evidence(artifacts):
    samples, points = [], []
    requests = {}
    for line in artifacts.get("http.log", b"").decode().splitlines():
        record = json.loads(line)
        if record.get("source") != "http-debug":
            continue
        message = record.get("msg", "")
        if message.startswith("Request:\n"):
            request = re.search(r"(?m)^([A-Z]+) (\S+) HTTP/", message)
            if request:
                requests[record["request_id"]] = {"method": request[1], "path": request[2]}
            continue
        if not message.startswith("Response:\n"):
            continue
        headers = record["msg"].split("\n\n", 1)[0]
        identity = re.search(r"(?im)^X-P95-Request-ID:\s*(request-[a-f0-9]{20})\s*$", headers)
        if identity:
            samples.append({"request": identity[1], "worker": str(record["vu"]),
                            "iteration": record["iter"], "scenario": record["scenario"],
                            "name": record.get("name"), **requests.get(record.get("request_id"), {})})
    for line in artifacts.get("points.json", b"").decode().splitlines():
        record = json.loads(line)
        if record.get("type") == "Point":
            points.append({"metric": record["metric"], **record["data"]})
    return samples, points


def jmeter_evidence(artifacts):
    data = artifacts.get("samples.jtl", b"")
    if b"<!" in data:
        raise ValueError("Unexpected XML declaration in native evidence")
    root = ET.fromstring(data)
    if root.tag != "testResults":
        raise ValueError("Expected native JMeter testResults")
    samples = []
    for sample in root.iter("httpSample"):
        identity = re.search(r"(?im)^X-P95-Request-ID:\s*(request-[a-f0-9]{20})\s*$",
                             sample.findtext("responseHeader", ""))
        if identity:
            samples.append({"request": identity[1], "worker": sample.get("tn"),
                            "active_users": int(sample.get("na", 0)),
                            "success": sample.get("s") == "true", "status": sample.get("rc"),
                            "assertion_failures": sum(
                                item.findtext("failure") == "true" or item.findtext("error") == "true"
                                for item in sample.findall("assertionResult"))})
    return samples, []


def run_artifact(tool, source, directory, *, fixtures=None, profile=None, seed="public-reference",
                 timeout=60):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    filename = "model.js" if tool == "k6" else "model.jmx"
    inputs = dict(fixtures or {})
    if filename in inputs:
        raise ValueError("Reserved model artifact fixture name")
    inputs[filename] = artifact_source(tool, source).encode()
    exports = ({"points.json": 8 * 1048576, "http.log": 8 * 1048576} if tool == "k6" else
               {"samples.jtl": 8 * 1048576, "engine.log": 1048576})
    image = tool_image(tool)
    with execution_trial(directory, profile=Profile(**(profile or {})), seed=seed,
                         image=image, resources=Resources(768, 256, 512)) as (shop, worker):
        started = time.monotonic()
        result = worker.execute(native_command(tool), inputs, timeout=timeout,
                                output_limit=1048576, exports=exports)
        try:
            events = shop.audit()
        except RuntimeError as error:
            if not shop.audit_overflow:
                raise WorkerUnavailable(str(error)) from error
            events = shop.audit(wait=False)
        overflow = shop.audit_overflow
    for name, data in result.artifacts.items():
        (directory / name).write_bytes(data)
    (directory / "audit.json").write_text(json.dumps(events, indent=2) + "\n")
    (directory / "execution.json").write_text(json.dumps({"image": image,
        "status": result.status, "returncode": result.returncode,
        "duration_seconds": result.duration_seconds, "artifact_errors": result.artifact_errors,
        "output": result.output, "seed": seed, "profile": profile or {},
        "audit_overflow": overflow}, indent=2) + "\n")
    if overflow:
        raise AuditOverflow("Audit overflow; script exceeded the request evidence limit")
    try:
        samples, points = (k6_evidence(result.artifacts) if tool == "k6" else
                           jmeter_evidence(result.artifacts))
        loaded = bool(result.artifacts.get("points.json") if tool == "k6" else
                      result.artifacts.get("samples.jtl"))
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError, UnicodeDecodeError, ET.ParseError):
        loaded, samples, points = False, [], []
    return Trial(loaded, result.status, result.returncode, events, samples, points, started)
