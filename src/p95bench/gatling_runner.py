"""Compile and execute one sandboxed Gatling Java simulation."""

import json
import re
import struct
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


class BinaryLog:
    def __init__(self, path):
        self.data = path.read_bytes()
        self.offset = 0
        self.strings = {}

    def take(self, size):
        value = self.data[self.offset:self.offset + size]
        if len(value) != size:
            raise ValueError("Truncated Gatling simulation log")
        self.offset += size
        return value

    def integer(self):
        return struct.unpack(">i", self.take(4))[0]

    def long(self):
        return struct.unpack(">q", self.take(8))[0]

    def boolean(self):
        return self.take(1) == b"\x01"

    def string(self):
        size = self.integer()
        if not size:
            return ""
        value, coder = self.take(size), self.take(1)[0]
        if coder == 0:
            return value.decode("latin-1")
        if coder == 1:
            return value.decode("utf-16-le")
        raise ValueError("Unknown Gatling string encoding")

    def cached_string(self):
        index = self.integer()
        if index < 0:
            return self.strings[-index]
        value = self.string()
        self.strings[index] = value
        return value

    def groups(self):
        return [self.cached_string() for _ in range(self.integer())]

    def parse(self):
        records, run_start, scenarios = [], None, []
        while self.offset < len(self.data):
            header = self.take(1)[0]
            if header == 0:
                self.strings.clear()
                version, simulation = self.string(), self.string()
                run_start = self.long()
                self.string()
                scenarios = [self.string() for _ in range(self.integer())]
                for _ in range(self.integer()):
                    self.take(self.integer())
                records.append({"type": "run", "version": version,
                                "simulation": simulation, "start": run_start,
                                "scenarios": scenarios})
            elif header == 1:
                groups = self.groups()
                name = self.cached_string()
                start, end, success = self.integer(), self.integer(), self.boolean()
                self.cached_string()
                records.append({"type": "request", "name": name, "groups": groups,
                                "start": start, "end": end, "success": success,
                                "response_time": end - start})
            elif header == 2:
                records.append({"type": "user", "scenario": self.integer(),
                                "start": self.boolean(), "time": self.integer()})
            elif header == 3:
                self.groups()
                self.integer()
                self.integer()
                self.integer()
                self.boolean()
            elif header == 4:
                self.cached_string()
                self.integer()
            else:
                raise ValueError("Unknown Gatling simulation record")
        active = 0
        for record in records:
            if record["type"] == "user":
                active += 1 if record["start"] else -1
            elif record["type"] == "request":
                record["active_users"] = max(0, active)
        if run_start is None:
            raise ValueError("Missing Gatling run header")
        return run_start, scenarios, [record for record in records if record["type"] == "request"]


PREFIX = re.compile(
    r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) "
    r"(?P<level>\w+)\s+(?P<logger>\S+) - (?P<message>.*)$"
)
TRACE_LOGGER = "io.gatling.http.engine.response.DefaultStatsProcessor"
HTTP_APP_LOGGER = "io.gatling.http.client.impl.HttpAppHandler"
REQUEST_NAME = re.compile(r"(?m)^Request:\s*\n(.+):\s*(OK|KO)\s*$")
REQUEST_FAILURE = re.compile(r"(?m)^Request '(.+)' failed for user (\d+):")
FAILED_STATUS = re.compile(r"found (\d{3})\b")
RESPONSE_STATUS = re.compile(r"(?m)^HTTP/\d(?:\.\d)?\s+(\d{3})\b")
SESSION_USER = re.compile(
    r"(?m)^Session:\s*\nSession\([^,\n]+,\s*(\d+),\s*(?:Map|HashMap)\("
)
REQUEST_LINE = re.compile(r"(?m)^([A-Z]+)\s+(\S+)\s*$")
REQUEST_ID = re.compile(r"(?im)^[ \t]*X-P95-Request-ID:\s*(request-[a-f0-9]{20})\s*$")
REQUEST_PATHS = {
    "cart": ("POST", "/v2/carts"),
    "cart recovery": ("POST", "/v2/carts"),
    "cart retry": ("PATCH", "/v2/carts"),
    "cart update": ("PATCH", "/v2/carts"),
    "catalog": ("GET", "/v2/catalog"),
    "catalog retry": ("GET", "/v2/catalog"),
    "checkout": ("POST", "/v2/orders"),
    "commit": ("POST", "/v2/orders"),
    "login": ("POST", "/v2/login"),
    "login form": ("GET", "/v2/login-form"),
    "renew": ("POST", "/v2/session/renew"),
    "reservation": ("POST", "/v2/reservations"),
    "reservation assertion": ("GET", "/v2/jobs"),
    "reservation poll": ("GET", "/v2/jobs"),
    "work": ("POST", "/v2/work"),
    "work retry": ("POST", "/v2/work"),
}


def trace_records(path):
    records, current = [], None
    for line in path.read_text(errors="replace").splitlines():
        match = PREFIX.match(line)
        if match:
            if current is not None:
                records.append(current)
            current = match.groupdict()
        elif current is not None:
            current["message"] += "\n" + line
    if current is not None:
        records.append(current)
    return records


def request_trace(record):
    if record["logger"] != TRACE_LOGGER:
        return None
    message = record["message"]
    name = REQUEST_NAME.search(message)
    failed = REQUEST_FAILURE.search(message)
    if name:
        request_name, success = name[1], name[2] == "OK"
        status = None
    elif failed:
        request_name, success = failed[1], False
        status_match = FAILED_STATUS.search(message)
        status = int(status_match[1]) if status_match else None
    else:
        return None
    user = SESSION_USER.search(message)
    user_id = user[1] if user else failed[2] if failed else None
    request_line = REQUEST_LINE.search(
        message.split("HTTP request:", 1)[-1].split("HTTP response:", 1)[0])
    response = message.split("HTTP response:", 1)[-1]
    identity = REQUEST_ID.search(response)
    response_status = RESPONSE_STATUS.search(response)
    if status is None and response_status:
        status = int(response_status[1])
    method, path = (request_line[1], urlsplit(request_line[2]).path) if request_line else (
        REQUEST_PATHS.get(request_name, (None, None))
    )
    if not user_id or not method or not path:
        return None
    timestamp = datetime.strptime(record["time"], "%Y-%m-%d %H:%M:%S.%f")
    timestamp_ms = int(timestamp.replace(tzinfo=UTC).timestamp() * 1000)
    return {"time": timestamp_ms, "name": request_name, "success": success,
            "worker": f"user-{user_id}", "method": method, "path": path,
            "request": identity[1] if identity else None, "status": status}


def response_trace(record):
    if record["logger"] != HTTP_APP_LOGGER:
        return None
    identity = REQUEST_ID.search(record["message"])
    status = RESPONSE_STATUS.search(record["message"])
    if not identity or not status:
        return None
    timestamp = datetime.strptime(record["time"], "%Y-%m-%d %H:%M:%S.%f")
    timestamp_ms = int(timestamp.replace(tzinfo=UTC).timestamp() * 1000)
    return {"time": timestamp_ms, "request": identity[1], "status": int(status[1])}


def attach_response_ids(traces, responses):
    available = set(range(len(responses)))
    for trace in traces:
        if trace["request"] is not None:
            continue
        candidates = [index for index in sorted(available)
                      if trace["status"] is None or responses[index]["status"] == trace["status"]]
        if not candidates:
            continue
        index = min(candidates, key=lambda item: abs(responses[item]["time"] - trace["time"]))
        if abs(responses[index]["time"] - trace["time"]) > 60000:
            continue
        trace["request"] = responses[index]["request"]
        available.remove(index)
    return traces


def request_samples(log_path, data_path):
    run_start, _, requests = BinaryLog(data_path).parse()
    traces = []
    records = trace_records(log_path)
    responses = [response for record in records if (response := response_trace(record))]
    for record in records:
        trace = request_trace(record)
        if trace:
            traces.append(trace)
    attach_response_ids(traces, responses)

    available = set(range(len(requests)))
    samples = []
    for trace in traces:
        candidates = [index for index in available
                      if requests[index]["name"] == trace["name"]
                      and requests[index]["success"] == trace["success"]]
        if not candidates:
            raise ValueError("Gatling trace has no matching simulation record")
        index = min(candidates, key=lambda item: abs(run_start + requests[item]["end"] - trace["time"]))
        event = requests[index]
        if abs(run_start + event["end"] - trace["time"]) > 60000:
            raise ValueError("Gatling trace timestamp does not match simulation record")
        available.remove(index)
        samples.append({"request": trace["request"] or f"request-{index:020x}",
                        "worker": trace["worker"],
                        "name": trace["name"], "method": trace["method"], "path": trace["path"],
                        "success": trace["success"],
                        "assertion_failures": int(not trace["success"]),
                        "response_time": event["response_time"],
                        "active_users": event["active_users"]})
    for index in sorted(available):
        event = requests[index]
        method, path = REQUEST_PATHS.get(event["name"], ("GET", "/v2/unknown"))
        samples.append({"request": f"request-{index:020x}", "worker": "user-1",
                        "name": event["name"], "method": method, "path": path,
                        "success": event["success"],
                        "assertion_failures": int(not event["success"]),
                        "response_time": event["response_time"],
                        "active_users": event["active_users"]})
    return samples


def main():
    source = Path(sys.argv[1])
    classes = Path("/tmp/gatling-classes")
    classes.mkdir()
    classpath = "/opt/gatling/lib/*"
    compile_started = time.monotonic()
    compiled = subprocess.run(["javac", "-J-Xmx256m", "-cp", classpath, "-d", str(classes),
                               str(source)], check=False, capture_output=True, text=True)
    compile_seconds = time.monotonic() - compile_started
    print(f"GATLING_COMPILE_SECONDS={compile_seconds:.3f}")
    if compiled.returncode:
        print("Gatling compilation failed")
        print((compiled.stderr or compiled.stdout)[-8192:])
        return 1

    results = Path("/tmp/gatling-results")
    results.mkdir()
    completed = subprocess.run([
        "java", "-Xmx256m", "-XX:ActiveProcessorCount=1",
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "-Duser.timezone=UTC",
        "-Djava.util.prefs.userRoot=/tmp/java-user-prefs",
        "-Dlogback.configurationFile=/opt/gatling/logback.xml",
        "-cp", f"{classes}:{classpath}", "io.gatling.app.Gatling",
        "-s", "ModelSimulation", "-nr", "-rf", str(results),
    ], check=False, capture_output=True, text=True)
    log_path = Path("/output/gatling.log")
    simulation_logs = list(results.glob("*/simulation.log"))
    samples = []
    try:
        if log_path.is_file() and simulation_logs:
            samples = request_samples(log_path, simulation_logs[-1])
        Path("/output/requests.jsonl").write_text(
            "".join(json.dumps(sample, separators=(",", ":")) + "\n" for sample in samples)
        )
    except (OSError, ValueError, IndexError, KeyError, struct.error) as error:
        print(f"Gatling evidence parsing failed: {error}")
        return 1

    print(f"GATLING_NATIVE_EXIT_CODE={completed.returncode}")
    if completed.returncode:
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        print(output[-8192:])
        return 99 if samples and simulation_logs else completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
