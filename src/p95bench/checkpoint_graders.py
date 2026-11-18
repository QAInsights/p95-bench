"""Public v2 rubrics: server truth, native worker provenance and declared gates."""

import json
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from uuid import uuid4

from p95bench.capacity import capacity_preflight, operations
from p95bench.execution import run_artifact
from p95bench.grades import Checkpoint, CheckpointGrade


def award(rubric, fractions):
    earned = {}
    for checkpoint in rubric:
        raw = max(0, min(1, fractions.get(checkpoint["id"], 0)))
        earned[checkpoint["id"]] = min([raw] + [earned[parent] for parent in checkpoint.get("requires", [])])
    return tuple(Checkpoint(item["id"], round(item["points"] * earned[item["id"]], 8),
                            item["points"]) for item in rubric)


def ratio(actual, expected):
    return min(1, actual / expected) if expected else 0


def events(trial, kind):
    return [event for event in trial.events if event["kind"] == kind]


def provenance(trial):
    native = {sample["request"]: sample for sample in trial.samples}
    logins = {event["session"]: native.get(event["request"], {}).get("worker") for event in events(trial, "login")}
    carts = {event["resource"]: native.get(event["request"], {}).get("worker") for event in events(trial, "cart")}
    for event in events(trial, "authenticated"):
        worker = native.get(event["request"], {}).get("worker")
        if not worker or worker != logins.get(event["session"]):
            return False
    for event in events(trial, "commit"):
        worker = native.get(event["request"], {}).get("worker")
        if not worker or worker != carts.get(event["resource"]):
            return False
    return bool(logins) and all(logins.values())


def concurrent_workers(trial, expected):
    native = {sample["request"]: sample["worker"] for sample in trial.samples}
    intervals = defaultdict(list)
    for event in trial.events:
        if event["kind"] in ("login", "commit") and event["request"] in native:
            intervals[native[event["request"]]].append(event["time"])
    if len(intervals) != expected:
        return False
    return max(min(values) for values in intervals.values()) < min(max(values) for values in intervals.values())


def asserted(trial):
    if trial.points:
        return any(point["metric"] == "checks" and point["value"] == 0 for point in trial.points) and trial.returncode == 99
    return any(sample["assertion_failures"] > 0 for sample in trial.samples)


def rate_fraction(observed, target, window=None):
    error = abs(observed - target) / target
    tolerance = max(.1, 1 / (target * window)) if window is not None else .1
    if error <= tolerance or math.isclose(error, tolerance, abs_tol=1e-12):
        return 1
    return 0 if error >= .4 else (.4 - error) / (.4 - tolerance)


def workload_fractions(trial, contract):
    pools = contract.get("pools") or {"work": {"path": contract.get("path", "/v2/work"),
        "rate": contract.get("rate", 5), "operation": contract.get("operation", "work")}}
    collected = {name: operations(trial, pool["path"], contract.get("completion", False))
                 for name, pool in pools.items()}
    all_operations = [event for values in collected.values() for event in values]
    if not all_operations:
        return {"loads": 0, "setup": 0, "behavior": 0, "safety": 0}
    anchor = min(event["time"] for event in all_operations)
    warmup, duration = contract.get("warmup", 5), contract.get("measured", 30)
    width = contract.get("window", 5)
    rates, smooth = [], []
    for name, pool in pools.items():
        values = collected[name]
        offset, targets = len(rates), []
        for index in range(int(duration / width)):
            start = anchor + warmup + index * width
            target = contract.get("rates", [pool["rate"]])[min(index // 2, len(contract.get("rates", [pool["rate"]])) - 1)]
            targets.append(target)
            count = sum(start <= value["time"] < start + width for value in values)
            rates.append(rate_fraction(count / width, target, width))
            bursts = [sum(start + second <= value["time"] < start + second + 1 for value in values)
                      for second in range(width)]
            smooth.append(all(count <= math.ceil(target * 1.5) + 1 for count in bursts)
                          and count / width >= target * .75)
        total = sum(anchor + warmup <= value["time"] < anchor + warmup + duration for value in values)
        overall = rate_fraction(total / duration, mean(targets), duration)
        rates[offset:] = [min(fraction, overall) for fraction in rates[offset:]]
    ends = events(trial, "end")
    unexpected = [event for event in ends if event["outcome"] >= 400 and not (
        contract.get("completion") and (event["outcome"] == 429 or
                                       event["outcome"] == 401 and event.get("error") == "expired"))]
    allowed = {"/v2/login-form", "/v2/login", "/v2/session/renew"} | {pool["path"] for pool in pools.values()}
    journeys = any(pool.get("operation") == "journey" for pool in pools.values())
    extra_routes = [event for event in events(trial, "start") if event["path"] not in allowed
                    and not (journeys and (event["path"] == "/v2/catalog" or event["path"].startswith("/v2/carts")))]
    native_drops = sum(point["value"] for point in trial.points if point["metric"] == "dropped_iterations")
    native_checks = [point for point in trial.points if point["metric"] == "checks"]
    max_vus = max([point["value"] for point in trial.points if point["metric"] in ("vus", "vus_max")]
                  + [sample.get("active_users", 0) for sample in trial.samples] + [0])
    duration_ok = max(event["time"] for event in all_operations) >= anchor + warmup + duration - 1
    drain_ok = max(event["time"] for event in ends) <= anchor + warmup + duration + 5
    limits = (len(unexpected) <= len(all_operations) * .01 and not extra_routes
              and native_drops <= len(all_operations) * .01 and duration_ok and drain_ok
              and (not trial.started or anchor - trial.started <= 10)
              and max_vus <= contract.get("max_vus", 64 if contract.get("pools") else 32)
              and trial.status == "completed" and trial.returncode == 0
              and sum(point["value"] == 0 for point in native_checks) <= len(native_checks) * .01)
    if contract.get("completion"):
        starts = {event["request"]: event for event in events(trial, "start")}
        auth = {event["request"]: event for event in events(trial, "authenticated")}
        workers = {sample["request"]: sample["worker"] for sample in trial.samples}
        last_limit = {}
        for end in ends:
            start, state = starts[end["request"]], auth.get(end["request"])
            if not state or start["path"] != "/v2/work":
                continue
            actor = workers.get(end["request"])
            if start["time"] < last_limit.get(actor, -1):
                limits = False
            if end["outcome"] == 429:
                last_limit[actor] = end["time"] + 1
        raw = sum(anchor + warmup <= event["time"] < anchor + warmup + duration
                  for event in starts.values() if event["path"] == "/v2/work")
        limits = limits and raw / duration <= contract.get("raw_ceiling", 12)
    safe = provenance(trial)
    return {"loads": float(trial.loaded and safe), "setup": mean(rates) if safe else 0,
            "behavior": mean(smooth) if safe else 0, "safety": float(limits and safe)}


def flow_fractions(trial, controls, contract):
    expected = contract.get("users", 2) * contract.get("iterations", 2)
    logins, carts, commits = events(trial, "login"), events(trial, "cart"), events(trial, "commit")
    safe = provenance(trial)
    unique = len({event["resource"] for event in commits}) == len(commits) and len(commits) <= expected
    success = ratio(len(commits), expected) if safe and unique else 0
    paths = {event["request"]: event["path"] for event in events(trial, "start")}
    authorized_sessions = {event["session"] for event in events(trial, "mutation_authorized")
                           if paths[event["request"]] == "/v2/carts"}
    setup = min(ratio(len(logins), expected), ratio(len(authorized_sessions), expected))
    behavior = success
    safety = float(safe and unique and len(carts) == expected and concurrent_workers(trial, contract.get("users", 2)))
    mode = contract.get("mode", "flow")
    records = contract.get("records", [])
    allowed_actors = {row["username"] for row in records}
    if records:
        safety *= float({event["actor"] for event in logins} <= allowed_actors)
    if mode in ("expiry", "ownership", "denial"):
        recoveries = [event for event in events(trial, "end") if event.get("error") == "session"] if mode == "ownership" else events(trial, "renew")
        setup = ratio(len(recoveries), expected)
        behavior *= float(len(carts) == expected)
    elif mode == "partition":
        distinct = {event["actor"] for event in logins} & allowed_actors
        setup = ratio(len(distinct), len(allowed_actors))
        behavior = min(success, setup)
    elif mode == "pagination":
        pages = [event for event in events(trial, "start") if event["path"].startswith("/v2/catalog/page")]
        setup = float(bool(pages) and not any(event["path"] == "/v2/catalog" for event in events(trial, "start")))
        safety *= float(len(pages) <= expected * 3)
    elif mode == "etag":
        setup = ratio(len(events(trial, "cart_conflict")), expected)
        behavior = min(success, ratio(len(events(trial, "cart_update")), expected))
        safety *= float(all(event["quantity"] == contract.get("quantity", 2) for event in commits)
                        and len(events(trial, "cart_update")) == expected)
    elif mode == "data":
        updates = events(trial, "cart_update")
        setup = ratio(sum(event["actor"] in allowed_actors for event in updates), expected)
        quantities = {row["username"]: row["quantity"] for row in records}
        behavior = min(success, ratio(sum(event["quantity"] == quantities.get(event["actor"]) for event in commits), expected))
        record_counts = Counter(row["username"] for row in records)
        safety *= float(Counter(event["actor"] for event in commits) == {
            actor: count * contract.get("iterations", 2) for actor, count in record_counts.items()})
    elif mode in ("redirect", "challenge"):
        setup = ratio(len(events(trial, "claim")), expected)
        behavior = success if all(event.get("grant") for event in carts) else 0
    elif mode == "reservation":
        setup = ratio(len(events(trial, "reservation")), expected)
        polls = {event["job"]: [] for event in events(trial, "reservation")}
        for event in events(trial, "start"):
            if event["method"] == "GET" and event["path"].startswith("/v2/jobs/"):
                job = event["path"].split("?", 1)[0].removeprefix("/v2/jobs/")
                if job not in polls:
                    safety = 0
                else:
                    polls[job].append(event["time"])
        for timestamps in polls.values():
            timestamps.sort()
            safety *= float(1 <= len(timestamps) <= 5 and all(
                right - left >= .05 - 1e-9 for left, right in zip(timestamps, timestamps[1:])))
        ready = {event["reservation"] for event in events(trial, "job_state") if event["phase"] == "ready"}
        behavior = min(success, ratio(sum(event.get("reservation") in ready for event in commits), expected))
    elif mode == "errors":
        setup = min(ratio(len(events(trial, "renew")), expected), ratio(sum(event.get("error") == "expired" for event in events(trial, "end")), expected))
        behavior = 1
        for item in [trial, *controls]:
            paths = {event["request"]: event["path"] for event in events(item, "start")}
            eligible = [event for event in events(item, "end") if paths[event["request"]] == "/v2/work"]
            points = [point["value"] for point in item.points if point["metric"] == "business_failure"]
            expected_errors = sum(event["outcome"] >= 400 and event.get("error") not in ("expired",)
                                  and event["outcome"] != 429 for event in eligible)
            behavior *= float(len(points) == len(eligible) and sum(points) == expected_errors)
        safety = float(safe and controls and all(asserted(item) for item in controls))
    if mode in ("decline", "denial", "threshold", "reservation"):
        control_ok = bool(controls) and trial.returncode == 0 and not asserted(trial)
        for item in controls:
            if mode != "threshold":
                control_ok &= not events(item, "commit")
            else:
                control_ok &= len(events(item, "commit")) == expected
            control_ok &= (item.returncode == 99 if mode == "threshold" else asserted(item)) and item.loaded and item.status == "completed"
        if mode == "decline":
            behavior = float(control_ok)
        elif mode == "threshold":
            tagged = [point for point in trial.points if point["metric"] == "http_req_duration"
                      and point.get("tags", {}).get("name") == "checkout"]
            named = {sample["request"] for sample in trial.samples if sample.get("name") == "checkout"
                     and sample.get("method") == "POST" and sample.get("path") == "/v2/orders"}
            setup = min(ratio(len(tagged), expected), ratio(len(named), expected))
            behavior = float(control_ok)
        safety *= float(control_ok)
    ends = events(trial, "end")
    allowed = {401} if mode in ("expiry", "ownership", "errors", "denial") else {412} if mode == "etag" else set()
    unexpected = [event for event in ends if event["outcome"] >= 400 and event["outcome"] not in allowed]
    safety *= float(not unexpected and trial.status == "completed" and len(ends) <= expected * 25)
    return {"loads": float(trial.loaded), "setup": setup, "behavior": behavior, "safety": safety}


def executable(text, params, workdir, fixtures):
    rubric, contract, tool = params["rubric"], dict(params["contract"]), params["tool"]
    if contract.get("workload"):
        capacity_preflight(tool, contract, params["trials"][0].get("profile", {}), Path(workdir) / "capacity")
    values = []
    inputs = {params.get("fixture_aliases", {}).get(name, Path(name).name): value.encode()
              for name, value in fixtures.items()}
    if "accounts.json" in inputs:
        contract["records"] = json.loads(inputs["accounts.json"])
    try:
        for index, case in enumerate(params["trials"]):
            common = {"fixtures": inputs, "profile": case.get("profile", {}), "seed": uuid4().hex,
                      "timeout": contract.get("timeout", 60)}
            trial = run_artifact(tool, text, Path(workdir) / f"case-{index}", **common)
            controls = [run_artifact(tool, text, Path(workdir) / f"case-{index}-control-{position}",
                **dict(common, profile={**case.get("profile", {}), **control}))
                for position, control in enumerate(params.get("controls", []))]
            fractions = (workload_fractions(trial, contract) if contract.get("workload")
                         else flow_fractions(trial, controls, contract))
            values.append(award(rubric, fractions))
    except (ValueError, KeyError, TypeError, ET.ParseError) as error:
        return CheckpointGrade(award(rubric, {}), str(error))
    checkpoints = tuple(Checkpoint(item["id"], mean(case[index].earned for case in values), item["points"])
                        for index, item in enumerate(rubric))
    return CheckpointGrade(checkpoints, "Per-case credit capped by prerequisites; see native artifacts and owner audit")
