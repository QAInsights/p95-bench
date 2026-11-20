"""Build native-looking Phase D analysis fixtures from the canonical v2 data."""

import csv
import io
import json
from pathlib import Path

import yaml

EXTENSIONS = {
    "endpoints": {
        "jmeter": "jtl.csv", "k6": "points.jsonl",
        "locust": "requests.csv", "gatling": "simulation.log",
    },
    "omission": {
        "jmeter": "jtl.csv", "k6": "points.jsonl",
        "locust": "requests.csv", "gatling": "simulation.log",
    },
    "thresholds": {
        "jmeter": "assertions.csv", "k6": "summary.json",
        "locust": "stats.csv", "gatling": "stats.json",
    },
    "errors": {
        "jmeter": "jtl.csv", "k6": "summary.json",
        "locust": "failures.csv", "gatling": "simulation.log",
    },
    "decomposition": {
        "jmeter": "statistics.json", "k6": "summary.json",
        "locust": "stats.csv", "gatling": "stats.json",
    },
}


def _csv_text(columns, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _canonical(task_path, task):
    source_dir = task_path.parent
    aliases = task["params"].get("fixture_aliases", {})
    fixtures = {}
    for name in task["fixtures"]:
        logical_name = aliases.get(name, name)
        fixtures[logical_name] = (source_dir / name).read_text()
    return fixtures


def _sample_rows(fixtures):
    return list(csv.DictReader(io.StringIO(fixtures["samples.csv"])))


def _jmeter_samples(rows):
    return _csv_text(
        ["timeStamp", "elapsed", "label", "responseCode", "success", "threadName",
         "parent", "sample_id", "warmup", "request_type"],
        [{
            "timeStamp": row["timestamp_ms"],
            "elapsed": row["elapsed_ms"],
            "label": row["route"],
            "responseCode": "200" if row["success"] == "true" else "500",
            "success": row["success"],
            "threadName": "worker",
            "parent": row["parent"],
            "sample_id": row["row_id"],
            "warmup": row["warmup"],
            "request_type": "transaction" if row["parent"] == "true" else "request",
        } for row in rows],
    )


def _locust_requests(rows):
    return _csv_text(
        ["row_id", "timestamp", "name", "request_type", "response_time", "success", "warmup"],
        [{
            "row_id": row["row_id"],
            "timestamp": row["timestamp_ms"],
            "name": row["route"],
            "request_type": "transaction" if row["parent"] == "true"
            else "POST" if row["route"] == "checkout" else "GET",
            "response_time": row["elapsed_ms"],
            "success": row["success"],
            "warmup": row["warmup"],
        } for row in rows],
    )


def _k6_points(rows):
    points = []
    for row in rows:
        points.append(json.dumps({
            "type": "Point",
            "metric": "http_req_duration",
            "data": {
                "time": row["timestamp_ms"],
                "value": float(row["elapsed_ms"]),
                "tags": {
                    "route": row["route"],
                    "row_id": row["row_id"],
                    "success": row["success"],
                    "warmup": row["warmup"],
                    "parent": row["parent"],
                },
            },
        }, sort_keys=True))
    return "\n".join(points) + "\n"


def _gatling_samples(rows):
    records = []
    for row in rows:
        start = float(row["timestamp_ms"])
        end = start + float(row["elapsed_ms"])
        start = int(start) if start.is_integer() else start
        end = int(end) if isinstance(end, float) and end.is_integer() else end
        tag = "GROUP" if row["parent"] == "true" else "REQUEST"
        status = "OK" if row["success"] == "true" else "KO"
        records.append(
            f"{tag} {row['route']} {row['row_id']} {start} {end} {status} "
            f"warmup={row['warmup']}"
        )
    return "\n".join(records) + "\n"


def _native_file(kind, tool, fixtures):
    if kind in ("endpoints", "omission"):
        rows = _sample_rows(fixtures)
        return {
            "jmeter": _jmeter_samples(rows),
            "k6": _k6_points(rows),
            "locust": _locust_requests(rows),
            "gatling": _gatling_samples(rows),
        }[tool]
    data = json.loads(fixtures["summary.json"])
    if kind == "thresholds":
        aggregate_p95 = data["metrics"]["http_req_duration"]["values"]["p(95)"]
        checkout_metric = data["metrics"]["http_req_duration{route:checkout}"]
        checkout_p95 = checkout_metric["values"]["p(95)"]
        limit = data["limit_ms"]
        threshold_passed = next(iter(checkout_metric["thresholds"].values()))
        if tool == "jmeter":
            return _csv_text(
                ["label", "elapsed", "success", "assertion", "metric"],
                [
                    {"label": "all-requests", "elapsed": aggregate_p95, "success": "true",
                     "assertion": "", "metric": "aggregate"},
                    {"label": "checkout", "elapsed": checkout_p95,
                     "success": str(threshold_passed).lower(),
                     "assertion": f"Duration Assertion failed (limit={limit})",
                     "metric": "checkout"},
                ],
            )
        if tool == "k6":
            return json.dumps(data, indent=2, sort_keys=True) + "\n"
        if tool == "locust":
            return _csv_text(
                ["Type", "Name", "Request Count", "Failure Count", "95%", "Requests/s"],
                [
                    {"Type": "POST", "Name": "all-requests", "Request Count": 10,
                     "Failure Count": 0, "95%": aggregate_p95, "Requests/s": 5},
                    {"Type": "POST", "Name": "checkout", "Request Count": 2,
                     "Failure Count": int(not threshold_passed),
                     "95%": checkout_p95, "Requests/s": 1},
                ],
            )
        if tool == "gatling":
            return json.dumps({
                "all-requests": {
                    "name": "all-requests", "percentiles": {"percentile3": aggregate_p95},
                },
                "checkout": {
                    "name": "checkout", "percentiles": {"percentile3": checkout_p95},
                    "failed": not threshold_passed,
                },
                "threshold": {"limitMs": limit},
            }, indent=2, sort_keys=True) + "\n"
        return json.dumps({
            "all-requests": {"percentiles": {"percentile3": aggregate_p95}},
            "checkout": {"percentiles": {"percentile3": checkout_p95},
                         "failed": not threshold_passed},
            "threshold": {"limitMs": limit},
        }, indent=2, sort_keys=True) + "\n"
    if kind == "errors":
        if tool == "jmeter":
            return _csv_text(
                ["sample_id", "label", "responseCode", "success", "errorClass", "count", "route"],
                [{
                    "sample_id": {
                        200: "work-ok", 401: "expiry-1", 500: "work-500",
                    }[row["status"]],
                    "label": "/v2/work",
                    "responseCode": row["status"],
                    "success": str(row["status"] < 400).lower(),
                    "errorClass": row["class"],
                    "count": row["count"],
                    "route": row["route"],
                } for row in data["outcomes"]],
            )
        if tool == "k6":
            outcomes = []
            sample_ids = {200: "work-ok", 401: "expiry-1", 500: "work-500"}
            for row in data["outcomes"]:
                outcomes.append({**row, "id": sample_ids[row["status"]]})
            unexpected = sum(
                row["count"] for row in data["outcomes"] if row["class"] == "unexpected"
            )
            total = sum(row["count"] for row in data["outcomes"])
            return json.dumps({
                "metrics": {
                    "business_failure": {"values": {"count": unexpected}},
                    "http_reqs": {"values": {"count": total}},
                },
                "outcomes": outcomes,
            }, indent=2, sort_keys=True) + "\n"
        if tool == "locust":
            return _csv_text(
                ["Request Type", "Name", "Error", "Occurrences", "Classification", "Route"],
                [{
                    "Request Type": "POST",
                    "Name": "/v2/work",
                    "Error": (
                        "401 session expired" if row["status"] == 401
                        else "500 unexpected response"
                    ),
                    "Occurrences": row["count"],
                    "Classification": row["class"],
                    "Route": row["route"],
                } for row in data["outcomes"] if row["status"] != 200],
            )
        records = []
        starts = {200: 1000, 401: 1010, 500: 1020}
        ids = {200: "work-ok", 401: "expiry-1", 500: "work-500"}
        for row in data["outcomes"]:
            status = row["status"]
            status_text = "OK" if status < 400 else "KO"
            records.append(
                f"REQUEST /v2/work {ids[status]} {starts[status]} {starts[status] + 10} "
                f"{status_text} status={status} class={row['class']} count={row['count']} "
                f"route={row['route']}"
            )
        records.append("GROUP work 1000 1100 OK requests=100 route=/v2/orders")
        return "\n".join(records) + "\n"
    if kind == "decomposition":
        windows = data["windows"]
        if tool in ("jmeter", "k6"):
            summary = {"windows": windows, "distractor": data["distractor"]}
        elif tool == "locust":
            return _csv_text(
                ["Name", "Route", "Interval", "Request Count", "95%",
                 "Distractor CPU %", "Distractor route"],
                [{
                    "Name": row["id"], "Route": row["route"], "Interval": row["interval"],
                    "Request Count": 4, "95%": row["p95_ms"],
                    "Distractor CPU %": data["distractor"]["cpu_percent"],
                    "Distractor route": data["distractor"]["route"],
                } for row in windows],
            )
        else:
            summary = {"contents": {
                row["id"]: {"route": row["route"], "interval": row["interval"],
                            "percentiles": {"percentile3": row["p95_ms"]}}
                for row in windows
            }, "distractor": data["distractor"]}
        return json.dumps(summary, indent=2, sort_keys=True) + "\n"
    raise ValueError(f"Unsupported analysis contract: {kind}")


def build_matrix_analysis_overlays(source_pack, target_pack):
    source_dir = Path(source_pack) / "tasks" / "analysis"
    overlay_root = Path(target_pack) / "overlays"
    result = {}
    for path in sorted(source_dir.glob("v2-*.yaml")):
        task = yaml.safe_load(path.read_text())
        code, slug = task["id"].split("-", 2)[1:]
        scenario = f"mx-{code}-{slug}"
        kind = task["params"]["analysis"]
        fixtures = _canonical(path, task)
        for tool, extension in EXTENSIONS[kind].items():
            name = f"{code}-{tool}.{extension}"
            files = {"data": name}
            text = _native_file(kind, tool, fixtures)
            output = overlay_root / tool / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text)
            if kind == "omission":
                schedule_name = f"{code}-schedule.json"
                schedule = fixtures["schedule.json"]
                schedule_path = overlay_root / tool / schedule_name
                schedule_path.write_text(schedule)
                files["schedule"] = schedule_name
            if kind == "thresholds" and tool == "locust":
                threshold_name = f"{code}-threshold.json"
                threshold_text = json.dumps({
                    "checkout": {"limit_ms": json.loads(fixtures["summary.json"])["limit_ms"]},
                }, indent=2, sort_keys=True) + "\n"
                (overlay_root / tool / threshold_name).write_text(threshold_text)
                files["threshold"] = threshold_name
            if kind == "errors" and tool == "locust":
                stats_name = f"{code}-stats.csv"
                stats_text = _csv_text(
                    ["Type", "Name", "Request Count", "Failure Count", "Requests/s"],
                    [{"Type": "POST", "Name": "/v2/work", "Request Count": 100,
                      "Failure Count": 10, "Requests/s": 5}],
                )
                (overlay_root / tool / stats_name).write_text(stats_text)
                files["stats"] = stats_name
            if kind == "decomposition":
                spans_name = "spans.json"
                spans = json.loads(fixtures["summary.json"])["spans"]
                spans_path = overlay_root / tool / spans_name
                spans_path.write_text(json.dumps(spans, indent=2, sort_keys=True) + "\n")
                files["spans"] = spans_name
            result[(scenario, tool)] = files
    return result
