"""Structured analysis answers checked against supplied, independently recomputed data."""

import csv
import io
import json
import math

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
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(same_shape(a, b) for a, b in zip(actual, expected)))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual)
    return type(actual) is type(expected)


def analysis(text, params, workdir, fixtures):
    fractions = {}
    try:
        if not isinstance(text, str) or len(text.encode()) > 65536:
            raise ValueError("Expected an answer of at most 64 KiB")
        value = json.loads(text, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError, OverflowError) as error:
        return CheckpointGrade(award(params["rubric"], {}),
                               f"Invalid model answer ({type(error).__name__}): {error}")
    try:
        logical = {params.get("fixture_aliases", {}).get(name, name): value for name, value in fixtures.items()}
        expected = truth(params["analysis"], logical)
        schema = same_shape(value, expected)
        fractions = {"loads": float(schema), "setup": float(schema and equal(value["observations"], expected["observations"])),
                     "behavior": float(schema and equal(value["diagnosis"], expected["diagnosis"])),
                     "safety": float(schema and equal(value["evidence"], expected["evidence"]))}
    except (ValueError, TypeError, KeyError):
        pass
    return CheckpointGrade(award(params["rubric"], fractions), "Structured values, enums and evidence IDs; numeric absolute tolerance 1e-6")
