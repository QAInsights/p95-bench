"""Structured analysis answers checked against supplied, independently recomputed data."""

import csv
import io
import json
import math
import re

from p95bench.checkpoint_graders import award
from p95bench.grades import CheckpointGrade


def nearest95(values):
    return sorted(values)[math.ceil(len(values) * .95) - 1]


def truth(kind, fixtures):
    if kind in ("endpoints", "omission"):
        rows = list(csv.DictReader(io.StringIO(fixtures["samples.csv"])))
        included = [row for row in rows if row["warmup"] == "false" and row["parent"] == "false"]
        if kind == "endpoints":
            routes = sorted({row["route"] for row in included})
            percentiles = {route: nearest95([float(row["elapsed_ms"]) for row in included if row["route"] == route]) for route in routes}
            ranking = sorted(routes, key=lambda route: (-percentiles[route], route))
            return {"observations": {"included": len(included), "errors": sum(row["success"] == "false" for row in included), "p95_ms": percentiles},
                    "diagnosis": {"ranking": ranking, "bottleneck": ranking[0]},
                    "evidence": {"rows": sorted(row["row_id"] for row in included if row["route"] == ranking[0]),
                                 "excluded": sorted(row["row_id"] for row in rows if row not in included)}}
        schedule = json.loads(fixtures["schedule.json"])
        rate = len(included) / schedule["duration_seconds"]
        return {"observations": {"actual_rate": rate, "observed_p95_ms": nearest95([float(row["elapsed_ms"]) for row in included]),
                                  "requested_rate": schedule["rate"]},
                "diagnosis": {"target_met": rate >= schedule["rate"], "risk": "coordinated_omission",
                              "missing_latency": "not_identifiable"},
                "evidence": {"rows": sorted(row["row_id"] for row in included), "schedule": "arrival-contract"}}
    data = json.loads(fixtures["summary.json"])
    if kind == "thresholds":
        aggregate = data["metrics"]["http_req_duration"]
        scoped = data["metrics"]["http_req_duration{route:checkout}"]
        return {"observations": {"aggregate_p95_ms": aggregate["values"]["p(95)"],
                                  "checkout_p95_ms": scoped["values"]["p(95)"], "limit_ms": data["limit_ms"]},
                "diagnosis": {"route": "checkout", "failed_metric": "http_req_duration{route:checkout}"},
                "evidence": {"keys": ["metrics.http_req_duration{route:checkout}.thresholds", "metrics.http_req_duration{route:checkout}.values.p(95)"],
                             "fix": "scope_checkout_threshold"}}
    if kind == "errors":
        total = sum(row["count"] for row in data["outcomes"])
        expected = sum(row["count"] for row in data["outcomes"] if row["class"] == "expected_expiry")
        bad = [row for row in data["outcomes"] if row["class"] == "unexpected"]
        unexpected = sum(row["count"] for row in bad)
        return {"observations": {"denominator": total, "expected": expected, "unexpected": unexpected, "corrected_rate": unexpected / total},
                "diagnosis": {"route": bad[0]["route"], "classification": "expected_recovery_is_not_failure"},
                "evidence": {"keys": [row["id"] for row in data["outcomes"] if row["class"] != "success"],
                             "fix": "exclude_expected_from_numerator_only"}}
    if kind == "decomposition":
        window = max(data["windows"], key=lambda window: window["p95_ms"])
        spans = [span for span in data["spans"] if span["window"] == window["id"]]
        dominant = max(spans, key=lambda span: span["duration_ms"])
        return {"observations": {"p95_ms": window["p95_ms"], "dominant_ms": dominant["duration_ms"]},
                "diagnosis": {"component": dominant["component"], "route": window["route"], "interval": window["interval"]},
                "evidence": {"keys": [window["id"], dominant["id"]], "next_test": "measure_db_wait_and_query_plan"}}
    raise ValueError("Unknown analysis fixture contract")


TOOL_ANSWER_STRINGS = {
    "thresholds": {
        "jmeter": ("DurationAssertion.checkout",
                   ["JTL.checkout.elapsed", "JTL.checkout.assertions"],
                   "attach_duration_assertion_to_checkout"),
        "k6": ("http_req_duration{route:checkout}",
               ["metrics.http_req_duration{route:checkout}.thresholds",
                "metrics.http_req_duration{route:checkout}.values.p(95)"],
               "scope_checkout_threshold"),
        "locust": ("checkout", ["stats.csv.checkout.95%", "threshold.checkout.limit_ms"],
                   "threshold_checkout_request_only"),
        "gatling": ("checkout.responseTime.percentile95",
                    ["stats.json.checkout.percentiles.percentile3",
                     "stats.json.threshold.limitMs"],
                    "assert_checkout_group_p95"),
    },
    "errors": {
        "jmeter": ("unexpected_orders_failures", ["JTL.expiry-1", "JTL.orders-500"],
                   "exclude_expected_from_numerator_only"),
        "k6": ("business_failure", ["status:401", "status:500"],
               "exclude_expected_from_numerator_only"),
        "locust": ("failed /v2/orders requests",
                   ["failures.csv./v2/orders-401", "failures.csv./v2/orders-500"],
                   "fail_unexpected_orders_only"),
        "gatling": ("failedRequests{request:/v2/orders}",
                    ["REQUEST./v2/orders.401", "REQUEST./v2/orders.500"],
                    "check_only_unexpected_orders_failures"),
    },
}


def _csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def _canonical_samples(rows):
    output = io.StringIO(newline="")
    columns = ("row_id", "timestamp_ms", "route", "elapsed_ms", "success", "warmup", "parent")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _analysis_samples(kind, tool, text):
    rows = []
    if tool == "jmeter":
        for row in _csv_rows(text):
            parent = row["request_type"] == "transaction" or row.get("parent") == "true"
            rows.append({
                "row_id": row["sample_id"],
                "timestamp_ms": row["timeStamp"],
                "route": row["label"],
                "elapsed_ms": row["elapsed"],
                "success": row["success"].lower(),
                "warmup": row["warmup"].lower(),
                "parent": str(parent).lower(),
            })
    elif tool == "k6":
        for line in text.splitlines():
            point = json.loads(line)
            tags = point["data"]["tags"]
            rows.append({
                "row_id": tags["row_id"], "timestamp_ms": str(point["data"]["time"]),
                "route": tags["route"], "elapsed_ms": str(_number(point["data"]["value"])),
                "success": tags["success"], "warmup": tags["warmup"],
                "parent": tags["parent"],
            })
    elif tool == "locust":
        for row in _csv_rows(text):
            parent = row["request_type"] == "Transaction"
            rows.append({
                "row_id": row["row_id"], "timestamp_ms": row["timestamp"],
                "route": row["name"], "elapsed_ms": row["response_time"],
                "success": row["success"].lower(), "warmup": row["warmup"].lower(),
                "parent": str(parent).lower(),
            })
    else:
        for line in text.splitlines():
            parts = line.split()
            record_type, route, row_id, start, end, status, warmup = parts
            rows.append({
                "row_id": row_id, "timestamp_ms": str(_number(start)), "route": route,
                "elapsed_ms": str(_number(float(end) - float(start))),
                "success": str(status == "OK").lower(),
                "warmup": warmup.partition("=")[2],
                "parent": str(record_type == "GROUP").lower(),
            })
    return {"samples.csv": _canonical_samples(rows)}


def _number(value):
    number = float(value)
    return int(number) if number.is_integer() else number


def _native_summary(kind, tool, text, fixtures, files):
    if tool == "k6" and kind == "thresholds":
        return {"summary.json": text}
    if tool == "k6" and kind == "errors":
        data = json.loads(text)
        outcomes = [{
            "id": f"status:{row['status']}",
            "status": row["status"],
            "class": row["class"],
            "route": row["route"],
            "count": row["count"],
        } for row in data["outcomes"]]
        return {"summary.json": json.dumps({
            "denominator": "all business responses, including expected expiry",
            "outcomes": outcomes,
        })}
    if kind == "thresholds":
        if tool == "jmeter":
            rows = {row["label"]: row for row in _csv_rows(text)}
            aggregate, checkout = rows["all-requests"], rows["checkout"]
            limit = int(checkout["assertion"].split("limit=", 1)[1].split(")", 1)[0])
            p95 = _number(aggregate["elapsed"])
            checkout_p95 = _number(checkout["elapsed"])
            threshold_passed = checkout["success"].lower() == "true"
        elif tool == "locust":
            rows = {row["Name"]: row for row in _csv_rows(text)}
            aggregate, checkout = rows["all-requests"], rows["checkout"]
            limit = int(json.loads(fixtures[files["threshold"]])["checkout"]["limit_ms"])
            p95 = _number(aggregate["95%"])
            checkout_p95 = _number(checkout["95%"])
            threshold_passed = checkout_p95 < limit
        else:
            data = json.loads(text)
            aggregate, checkout = data["all-requests"], data["checkout"]
            limit = _number(data["threshold"]["limitMs"])
            p95 = _number(aggregate["percentiles"]["percentile3"])
            checkout_p95 = _number(checkout["percentiles"]["percentile3"])
            threshold_passed = not checkout["failed"]
        threshold = f"p(95)<{limit}"
        return {"summary.json": json.dumps({
            "limit_ms": limit,
            "metrics": {
                "http_req_duration": {"values": {"p(95)": p95}},
                "http_req_duration{route:checkout}": {
                    "values": {"p(95)": checkout_p95},
                    "thresholds": {threshold: threshold_passed},
                },
            },
        })}
    if kind == "errors":
        if tool == "jmeter":
            outcomes = [{
                "id": f"status:{int(row['responseCode'])}",
                "status": int(row["responseCode"]), "class": row["errorClass"],
                "route": row["route"], "count": int(row["count"]),
            } for row in _csv_rows(text)]
        elif tool == "locust":
            stats = _csv_rows(fixtures[files["stats"]])[0]
            failures = _csv_rows(text)
            success_count = int(stats["Request Count"]) - int(stats["Failure Count"])
            outcomes = [{
                "id": "status:200", "status": 200, "class": "success",
                "route": failures[0]["Route"], "count": success_count,
            }]
            outcomes.extend(
                {
                    "id": f"status:{int(row['Error'].split()[0])}",
                    "status": int(row["Error"].split()[0]),
                    "class": row["Classification"], "route": row["Route"],
                    "count": int(row["Occurrences"]),
                } for row in failures
            )
        else:
            outcomes = []
            for line in text.splitlines():
                parts = line.split()
                if parts[0] != "REQUEST":
                    continue
                _, _, _, _, _, _, status, klass, count, route = parts
                outcomes.append({
                    "id": f"status:{int(status.split('=', 1)[1])}",
                    "status": int(status.split("=", 1)[1]),
                    "class": klass.split("=", 1)[1],
                    "route": route.split("=", 1)[1],
                    "count": int(count.split("=", 1)[1]),
                })
        return {"summary.json": json.dumps({
            "denominator": "all business responses, including expected expiry",
            "outcomes": outcomes,
        })}
    if kind == "decomposition":
        spans = json.loads(fixtures[files["spans"]])
        if tool == "locust":
            rows = _csv_rows(text)
            windows = [{
                "id": row["Name"], "route": row["Route"], "interval": row["Interval"],
                "p95_ms": _number(row["95%"]),
            } for row in rows]
            distractor = {
                "cpu_percent": _number(rows[0]["Distractor CPU %"]),
                "route": rows[0]["Distractor route"],
            }
        elif tool == "gatling":
            data = json.loads(text)
            windows = [{
                "id": name, "route": value["route"], "interval": value["interval"],
                "p95_ms": _number(value["percentiles"]["percentile3"]),
            } for name, value in data["contents"].items()]
            distractor = data["distractor"]
        else:
            data = json.loads(text)
            windows = data["windows"]
            distractor = data["distractor"]
        return {"summary.json": json.dumps({
            "windows": windows, "spans": spans, "distractor": distractor,
        })}
    raise ValueError(f"Unsupported native analysis adapter: {tool}/{kind}")


def adapt_native(kind, tool, fixtures, native_files):
    """Convert one Matrix tool's fixture bundle to the canonical v2 fixture files."""
    text = fixtures[native_files["data"]]
    if kind in ("endpoints", "omission"):
        canonical = _analysis_samples(kind, tool, text)
        if kind == "omission":
            canonical["schedule.json"] = fixtures[native_files["schedule"]]
        return canonical
    return _native_summary(kind, tool, text, fixtures, native_files)


def answer_for_tool(kind, tool, answer):
    if tool not in ("jmeter", "k6", "locust", "gatling"):
        return answer
    if kind not in TOOL_ANSWER_STRINGS:
        return answer
    metric, keys, fix = TOOL_ANSWER_STRINGS[kind][tool]
    answer["diagnosis"]["failed_metric"] = metric
    answer["evidence"]["keys"] = keys
    answer["evidence"]["fix"] = fix
    return answer


def analysis_reference(params, fixtures):
    if "native_files" not in params:
        aliases = params.get("fixture_aliases", {})
        logical = {aliases.get(name, name): value for name, value in fixtures.items()}
        return truth(params["analysis"], logical)
    logical = adapt_native(params["analysis"], params["tool"], fixtures, params["native_files"])
    return answer_for_tool(params["analysis"], params["tool"], truth(params["analysis"], logical))


def equal(actual, expected):
    if isinstance(expected, bool):
        return type(actual) is bool and actual == expected
    if isinstance(expected, (int, float)):
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and math.isclose(actual, expected, abs_tol=1e-6, rel_tol=0))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def invalid_constant(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def same_shape(actual, expected):
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and set(actual) == set(expected)
                and all(same_shape(actual[key], value) for key, value in expected.items()))
    if isinstance(expected, list):
        return isinstance(actual, list) and (
            not expected or all(same_shape(item, expected[0]) for item in actual))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual)
    return type(actual) is type(expected)


def _schema_error(actual, expected, path="$"):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"Expected object at {path}"
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing keys {missing}")
            if extra:
                parts.append(f"unexpected keys {extra}")
            return f"Schema mismatch at {path}: {', '.join(parts)}"
        for key, value in expected.items():
            error = _schema_error(actual[key], value, f"{path}.{key}")
            if error:
                return error
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f"Expected array at {path}"
        if expected:
            for index, item in enumerate(actual):
                error = _schema_error(item, expected[0], f"{path}[{index}]")
                if error:
                    return error
        return None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual):
            return None
        return f"Expected finite number at {path}"
    if type(actual) is not type(expected):
        return f"Expected {type(expected).__name__} at {path}"
    return None


def analysis(text, params, workdir, fixtures):
    fractions = {}
    try:
        if not isinstance(text, str) or len(text.encode()) > 65536:
            raise ValueError("Expected an answer of at most 64 KiB")
        fenced = re.fullmatch(r"\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*", text, re.IGNORECASE)
        raw = fenced[1] if fenced else text
        value = json.loads(raw, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError, OverflowError) as error:
        return CheckpointGrade(award(params["rubric"], {}),
                               f"Invalid model answer ({type(error).__name__}): {error}")
    expected = analysis_reference(params, fixtures)
    try:
        schema = same_shape(value, expected)
        if not schema:
            detail = _schema_error(value, expected) or "Expected the answer schema with finite numeric values"
            raise ValueError(detail)
        fractions = {"loads": float(schema), "setup": float(schema and equal(value["observations"], expected["observations"])),
                     "behavior": float(schema and equal(value["diagnosis"], expected["diagnosis"])),
                     "safety": float(schema and equal(value["evidence"], expected["evidence"]))}
    except (ArithmeticError, ValueError, TypeError, KeyError, RecursionError) as error:
        return CheckpointGrade(award(params["rubric"], {}),
                               f"Invalid model answer ({type(error).__name__}): {error}")
    return CheckpointGrade(award(params["rubric"], fractions), "Structured values, enums and evidence IDs; numeric absolute tolerance 1e-6")
