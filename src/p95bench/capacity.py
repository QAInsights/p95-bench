"""Workload qualification uses the same native engine, isolation and slowest service profile."""

import hashlib
import json
import math

from p95bench.execution import run_artifact
from p95bench.images import tool_image
from p95bench.reference_plans import reference
from p95bench.worker import WorkerUnavailable

QUALIFIED = {}


def prepare_workloads(tasks, directory):
    QUALIFIED.clear()
    for task in tasks:
        if task.grader == "v2_exec" and task.params["contract"].get("workload"):
            capacity_preflight(task.tool, task.params["contract"],
                               task.params["trials"][0].get("profile", {}), directory)


def operations(trial, path, completion=False):
    starts = {event["request"]: event for event in trial.events if event["kind"] == "start"}
    ends = {event["request"]: event for event in trial.events if event["kind"] == "end"}
    valid = [event for event in trial.events if event["kind"] == "commit" or
             (event["kind"] == "work" and event["fault"] is None)]
    result = []
    for event in valid:
        start, end = starts[event["request"]], ends.get(event["request"])
        if start["path"].split("?", 1)[0] == path and start["method"] == "POST" and end and end["outcome"] < 400:
            result.append({**event, "time": end["time"] if completion else start["time"]})
    return result


def capacity_preflight(tool, contract, profile, directory):
    pools = contract.get("pools") or {"work": {"path": contract.get("path", "/v2/work"),
        "rate": max(contract.get("rates", [contract.get("rate", 5)])),
        "operation": contract.get("operation", "work")}}
    worst = dict(profile)
    worst.update(delay_seconds=max(profile.get("delay_seconds", 0), profile.get("slow_seconds", 0)),
                 checkout_delay_seconds=max(profile.get("checkout_delay_seconds", 0), profile.get("checkout_slow_seconds", 0)),
                 slow_after=0, checkout_slow_after=0, slow_after_seconds=None,
                 checkout_slow_after_seconds=None)
    key = hashlib.sha256(json.dumps([tool_image(tool), pools, worst], sort_keys=True).encode()).hexdigest()
    if key in QUALIFIED:
        return QUALIFIED[key]
    scaled = {name: dict(pool, rate=math.ceil(pool["rate"] * 3.2)) for name, pool in pools.items()}
    config = {"workload": True, "duration": 12, "capacity": True,
              "completion": contract.get("completion", False)}
    if len(scaled) > 1:
        config["pools"] = scaled
    else:
        config.update(next(iter(scaled.values())))
    trial = run_artifact(tool, reference(tool, config), directory / key,
                         profile=worst, seed="public-capacity", timeout=40)
    counts = {}
    for name, pool in pools.items():
        events = operations(trial, pool["path"], contract.get("completion", False))
        if not events:
            raise WorkerUnavailable("Capacity preflight failed: no valid business operations")
        anchor = min(event["time"] for event in events)
        if trial.started and anchor - trial.started > 10:
            raise WorkerUnavailable("Capacity preflight failed: reference startup exceeded 10 seconds")
        count = sum(anchor + 5 <= event["time"] < anchor + 8 for event in events)
        counts[name] = count / 3
        if counts[name] < 3 * pool["rate"]:
            raise WorkerUnavailable(f"Capacity preflight failed: {name} {counts[name]:.2f}/s < {3 * pool['rate']}/s")
    if not trial.loaded or trial.status != "completed" or trial.returncode != 0:
        raise WorkerUnavailable("Capacity preflight failed: reference engine did not complete cleanly")
    report = {"image": tool_image(tool), "minimum_capacity_multiple": 3,
              "observed_rates": counts, "evidence_directory": str(directory / key),
              "resources": {"cpus": 1, "memory_mib": 768, "pids": 256, "nofile": 512},
              "duration_seconds": 12, "warmup_seconds": 5, "measurement_seconds": 3, "service_profile": worst,
              "pools": pools}
    (directory / key / "capacity.json").write_text(json.dumps(report, indent=2) + "\n")
    QUALIFIED[key] = report
    return report
