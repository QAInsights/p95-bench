"""Benchmark orchestration and reproducible scoring from saved attempts."""

import csv
import io
import json
import subprocess
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from p95bench.budget import BudgetLedger
from p95bench.capacity import prepare_workloads
from p95bench.checkpoint_graders import award
from p95bench.execution import AuditOverflow
from p95bench.graders import (
    CHECKPOINT_GRADERS,
    GRADERS,
    Grade,
    grade,
    jmeter_executable,
    k6_executable,
)
from p95bench.grades import Checkpoint, CheckpointGrade
from p95bench.packs import snapshot, summarize_v2, validate_snapshot
from p95bench.providers import (
    DEFAULT_OUTPUT_TOKENS,
    Gemini,
    ProviderInputError,
    ProviderUnavailable,
    SpendLimitExceeded,
    gemini_prices,
)
from p95bench.tasks import CATEGORIES
from p95bench.worker import WorkerUnavailable

SYSTEM_PROMPT = (
    "You are a performance test engineer. Solve the task exactly as specified. "
    "For numeric questions return only the number, for multiple choice only the letter. "
    "For JMeter tasks return a complete runnable JMX in a single XML code block."
)


def summarize(attempts):
    categories = defaultdict(list)
    for attempt in attempts:
        categories[attempt["category"]].append(float(attempt["grade"]["passed"]))
    scores = {key: 100 * sum(values) / len(values) for key, values in categories.items()}
    weight = sum(CATEGORIES[key] for key in scores)
    return {
        "overall_score": sum(scores[k] * CATEGORIES[k] for k in scores) / weight if weight else 0,
        "category_scores": scores,
        "attempts": len(attempts),
        "passed": sum(a["grade"]["passed"] for a in attempts),
        "estimated_cost_usd": sum(a["cost_usd"] for a in attempts),
        "input_tokens": sum(a["response"]["input_tokens"] for a in attempts),
        "output_tokens": sum(a["response"]["output_tokens"] for a in attempts),
        "latency_seconds": sum(a["response"]["latency_seconds"] for a in attempts),
    }


def harness_revision(root=None):
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    unknown = {"harness_commit": None, "harness_dirty": None}
    try:
        toplevel = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        if Path(toplevel).resolve() != root:
            return unknown
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                                     "--untracked-files=no"], capture_output=True, text=True,
                                    check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return unknown
    return {"harness_commit": commit, "harness_dirty": dirty}


def score_run(directory: Path):
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()
                if line.strip()]
    manifest_path = directory / "pack.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if manifest:
        validate_snapshot(manifest)
    if any(a.get("pack_id", "p95-v1") != (manifest["pack_id"] if manifest else "p95-v1")
           for a in attempts):
        raise ValueError("Mixed pack input or missing pack snapshot")
    summary = (summarize_v2(attempts, manifest)
               if manifest and manifest["grade_schema_version"] == 2 else summarize(attempts))
    skipped = directory / "not-run.jsonl"
    not_run = [json.loads(line) for line in skipped.read_text().splitlines()
               if line.strip()] if skipped.exists() else []
    responses = [a.get("response") or {} for a in attempts + not_run]
    if manifest and manifest["grade_schema_version"] == 2:
        responses.extend((a.get("repair") or {}).get("response") or {} for a in attempts)
    if any("cache_write_tokens" in response for response in responses):
        for key in ("cached_tokens", "cache_write_tokens"):
            summary[key] = sum(response.get(key, 0) for response in responses)
    if not_run or manifest and manifest["grade_schema_version"] == 2:
        summary["not_run"] = len(not_run)
    for entry in not_run:
        summary["estimated_cost_usd"] += entry.get("cost_usd", 0)
        for key in ("input_tokens", "output_tokens", "latency_seconds"):
            summary[key] += entry.get("response", {}).get(key, 0)
        if entry.get("grading_error") and "stop_reason" not in summary:
            summary["stop_reason"] = entry.get("error", entry["grading_error"]["message"])
            fallback = entry["grading_error"].get("fallback_error")
            if fallback:
                summary["stop_reason"] += f"; fallback error ({fallback['type']}): {fallback['message']}"
    if manifest and manifest["grade_schema_version"] == 2:
        entries = attempts + not_run
        if all(entry.get("tool") for entry in entries):
            costs = defaultdict(float)
            for entry in entries:
                costs[entry["tool"]] += entry.get("cost_usd", 0) + (entry.get("repair") or {}).get("cost_usd", 0)
            summary["cost_by_tool"] = dict(sorted(costs.items()))
    (directory / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    return summary


def _grade_response(task, text, workdir, v2):
    try:
        result = grade(task, text, workdir, 2) if v2 else grade(task, text, workdir)
        return result.to_dict(), None
    except Exception as error:  # noqa: BLE001
        detail = f"Grader error ({type(error).__name__}): {error}"
        metadata = {"type": type(error).__name__, "message": str(error)}
        if (isinstance(error, (WorkerUnavailable, OSError, subprocess.SubprocessError))
                and not isinstance(error, AuditOverflow)):
            return {"status": "not_run", "detail": detail}, metadata
        if not isinstance(error, AuditOverflow):
            metadata["stop_run"] = True
        try:
            result = (CheckpointGrade(award(task.params.get("rubric", [
                {"id": "grading_error", "points": 100}]), {}), detail)
                if v2 else Grade(False, detail))
        except Exception as fallback_error:  # noqa: BLE001
            result = CheckpointGrade((Checkpoint("grading_error", 0, 100),), detail)
            metadata.update(stop_run=True, fallback_error={
                "type": type(fallback_error).__name__, "message": str(fallback_error)})
        return result.to_dict(), metadata


def _fixture_secrets(task):
    sensitive = ("password", "secret", "token", "credential", "api_key", "api-key", "authorization")
    found = set()

    def visit(value, key=""):
        if isinstance(value, dict):
            for name, item in value.items():
                visit(item, str(name).lower())
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, str) and any(name in key for name in sensitive):
            found.add(value)

    for fixture in task.fixtures.values():
        try:
            visit(json.loads(fixture))
        except (ValueError, TypeError):
            reader = csv.DictReader(io.StringIO(fixture))
            for row in reader:
                visit(row)
            for line in fixture.splitlines():
                for separator in (":", "="):
                    key, marker, value = line.partition(separator)
                    if marker and any(name in key.lower() for name in sensitive):
                        found.add(value.strip().strip("\"', "))
    return sorted((value for value in found if value), key=len, reverse=True)


def _safe_feedback(text, task, *, tail=False):
    for secret in _fixture_secrets(task):
        text = text.replace(secret, "[redacted fixture secret]")
    encoded = text.encode("utf-8", errors="replace")
    bounded = encoded[-4096:] if tail else encoded[:4096]
    return bounded.decode("utf-8", errors="ignore")


def _repair_trigger(task, result, workdir):
    if result.get("status") != "graded":
        return None
    detail = str(result.get("detail", ""))
    if detail.startswith("Invalid model artifact"):
        return "invalid_model_artifact", _safe_feedback(detail, task)
    if detail.startswith("Invalid model answer"):
        return "invalid_model_answer", _safe_feedback(detail, task)
    if task.grader != "v2_exec":
        return None

    grade = CheckpointGrade.from_dict(result)
    loads = next((item for item in grade.checkpoints if item.id == "loads"), None)
    if loads is None or loads.earned != 0:
        return None

    native_artifacts = {
        "gatling": "requests.jsonl",
        "jmeter": "samples.jtl",
        "k6": "points.json",
        "locust": "requests.jsonl",
    }
    for path in sorted(Path(workdir).glob("case-*/execution.json")):
        if not path.parent.name.removeprefix("case-").isdigit():
            continue
        try:
            execution = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        audit_path = path.parent / "audit.json"
        try:
            request_evidence = bool(json.loads(audit_path.read_text()))
        except (OSError, ValueError):
            request_evidence = False
        artifact_errors = execution.get("artifact_errors") or {}
        native = path.parent / native_artifacts.get(task.tool, "")
        missing_native = not native.name or not native.is_file() or native.stat().st_size == 0
        returncode = execution.get("returncode")
        failed_before_request = returncode not in (None, 0) and not request_evidence
        output = str(execution.get("output") or "")
        compile_failure = (task.tool == "gatling" and returncode not in (None, 0)
                           and "compil" in output.lower()
                           and any(word in output.lower() for word in ("error", "fail")))
        if not (artifact_errors or missing_native or failed_before_request or compile_failure):
            continue

        parts = []
        if artifact_errors:
            parts.extend(str(value) for value in artifact_errors.values())
        if output:
            parts.append(output)
        if not parts:
            parts.append("No request evidence was produced.")
        reason = ("gatling_compile_failure" if compile_failure else
                  "tool_failed_before_request" if failed_before_request else
                  "missing_request_evidence")
        return reason, _safe_feedback("\n\n".join(parts), task, tail=True)
    return None


def _output_cap(config, model=None):
    return config.output_cap(model) if config else DEFAULT_OUTPUT_TOKENS


def run(provider, provider_name, model, price, tasks, out: Path, repeats=1, *,
        pack_id="p95-v1", efficiency_budgets=None, budget_ledger=None, repair_rounds=None):
    manifest = snapshot(pack_id, tasks, repeats, efficiency_budgets)
    v2 = manifest["grade_schema_version"] == 2
    repair_rounds = (1 if v2 else 0) if repair_rounds is None else repair_rounds
    if (not isinstance(repair_rounds, int) or isinstance(repair_rounds, bool)
            or repair_rounds not in (0, 1)):
        raise ValueError("repair_rounds must be 0 or 1")
    registry = CHECKPOINT_GRADERS if v2 else GRADERS
    for task in tasks:
        if task.grader not in registry:
            raise ValueError(f"Unknown grader: {task.grader}")
    if any(task.grader == "jmeter_exec" for task in tasks):
        jmeter_executable()
    if any(task.grader == "k6_exec" for task in tasks):
        k6_executable()
    started = datetime.now(UTC)
    if provider_name == "gemini":
        if price is None:
            price = gemini_prices(started).get(model)
        if price is None:
            raise ValueError(f"No verified price for {model}; supply an explicit price")
        if isinstance(provider, Gemini):
            provider.configure_run(started, model=model, price=price)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = ((out / pack_id if v2 else out) / run_id).resolve()
    directory.mkdir(parents=True)
    if v2:
        prepare_workloads(tasks, directory / "capacity")
    (directory / "pack.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    config = getattr(provider, "config", None)
    ledger = (budget_ledger or BudgetLedger()) if v2 else None
    provider.measure_transport = v2
    metadata = {"run_date": started.isoformat(), "pack_id": pack_id,
                "pack_digest": manifest["digest"], "visibility": manifest["visibility"],
                "provider": provider_name, "model": model,
                "base_url": getattr(config, "base_url", None),
                "cost_note": getattr(config, "cost_note", None),
                "pricing_source": getattr(config, "pricing_source", None),
                "temperature": getattr(config, "temperature", None),
                "reasoning_mode": (config.reasoning_mode_for(model)
                        if config and hasattr(config, "reasoning_mode_for")
                        else getattr(config, "reasoning_mode", None)),
                "repair_rounds": repair_rounds,
                **(config.request_extra(model) if config else {})}
    task_scenarios = {task.id: task.scenario for task in tasks if task.scenario is not None}
    if task_scenarios:
        metadata["task_scenarios"] = dict(sorted(task_scenarios.items()))
    if provider_name == "gemini":
        metadata.update(pricing_date_utc=started.date().isoformat(),
                        price_per_million=asdict(price))
    if v2:
        metadata["output_cap"] = _output_cap(config, model)
        metadata["spend_cap_usd"] = ledger.limit / 1_000_000 if ledger.limit is not None else None
        metadata.update(harness_revision())
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    budget_error = None
    grading_stop = None
    with (directory / "attempts.jsonl").open("w") as stream:
        for index, task in enumerate(tasks):
            for repeat in range(repeats):
                reservation = None
                system = ("You are a performance test engineer. Follow the task's output contract."
                          if v2 else SYSTEM_PROMPT)
                messages = [{"role": "system", "content": system},
                            {"role": "user", "content": task.user_prompt()}]
                try:
                    if grading_stop is not None:
                        raise ProviderUnavailable(grading_stop)
                    if budget_error is not None:
                        raise SpendLimitExceeded(budget_error)
                    if ledger:
                        output_cap = _output_cap(config, model)
                        maximum = ((len(json.dumps(messages).encode()) + 4096)
                                   * price.input_upper_bound
                                   + output_cap * price.output_upper_bound) / 1_000_000
                        retries = getattr(config, "transport_retries", 0) if config else 0
                        reservation = ledger.reserve(maximum * (1 + retries))
                        provider.transport_attempts = 0
                        provider.uncertain_attempts = None
                        provider.response_latency_seconds = None
                    response = provider.send(model, messages)
                    if ledger:
                        uncertain = getattr(provider, "uncertain_attempts", None)
                        requests = getattr(provider, "transport_attempts", 0) or (
                            1 + (uncertain if uncertain is not None else 0))
                        billable_retries = (max(0, requests - 1) if uncertain is None
                                            else uncertain)
                        ledger.settle(reservation, price.cost(response) + maximum * billable_retries)
                except (ProviderUnavailable, SpendLimitExceeded) as error:
                    if isinstance(error, SpendLimitExceeded):
                        budget_error = str(error)
                    with (directory / "not-run.jsonl").open("a") as skipped:
                        entry = {"task_id": task.id, "repeat": repeat,
                                 "provider": provider_name, "model": model,
                                 "status": "not_run", "error": str(error)}
                        if task.scenario is not None:
                            entry["scenario"] = task.scenario
                        if v2:
                            entry.update(pack_id=pack_id, tool=task.tool, budget_reservation=reservation)
                        skipped.write(json.dumps(entry) + "\n")
                    continue
                workdir = directory / f"task-{index}-{repeat}"
                result, grading_error = _grade_response(task, response.text, workdir, v2)
                attempt = {"task_id": task.id, "category": task.category, "repeat": repeat,
                           "provider": provider_name, "model": model,
                           "messages": messages, "grader": task.grader, "params": task.params,
                           "price_per_million": asdict(price), "response": asdict(response),
                           "grade": result, "cost_usd": price.cost(response)}
                if task.scenario is not None:
                    attempt["scenario"] = task.scenario
                if grading_error is not None:
                    attempt["grading_error"] = grading_error
                if v2:
                    attempt.update(pack_id=pack_id, pack_digest=manifest["digest"], tool=task.tool,
                                   budget_reservation=reservation)
                    latency = getattr(provider, "response_latency_seconds", None)
                    attempt["response"].update(
                        response_latency_seconds=(response.latency_seconds if latency is None
                                                  else latency),
                        transport_attempts=requests,
                        backoff_seconds=getattr(provider, "backoff_seconds", 0))
                repair = {"triggered": False}
                if repair_rounds:
                    if (getattr(response, "finish_reason", None) in ("length", "MAX_TOKENS", "max_tokens")
                            or response.output_tokens >= _output_cap(config, model)):
                        repair["skipped"] = "truncated_at_cap"
                        trigger = None
                    else:
                        trigger = _repair_trigger(task, result, workdir)
                    if trigger is not None:
                        reason, feedback = trigger
                        appended = [
                            {"role": "assistant", "content": response.text},
                            {"role": "user", "content": (
                                "Your previous answer failed before it could be scored:\n\n"
                                f"{feedback}\n\n"
                                "Return the complete corrected answer under the same output contract.")},
                        ]
                        repair_messages = messages + appended
                        repair = {"triggered": True, "reason": reason, "feedback": feedback,
                                  "messages_appended": appended, "response": None, "grade": None,
                                  "cost_usd": 0}
                        repair_reservation = None
                        try:
                            repair_maximum = None
                            if ledger:
                                output_cap = _output_cap(config, model)
                                repair_maximum = ((len(json.dumps(repair_messages).encode()) + 4096)
                                                  * price.input_upper_bound
                                                  + output_cap * price.output_upper_bound) / 1_000_000
                                retries = getattr(config, "transport_retries", 0) if config else 0
                                repair_reservation = ledger.reserve(repair_maximum * (1 + retries))
                                provider.transport_attempts = 0
                                provider.uncertain_attempts = None
                                provider.response_latency_seconds = None
                            repair_response = provider.send(model, repair_messages)
                            repair_cost = price.cost(repair_response)
                            if ledger:
                                repair_uncertain = getattr(provider, "uncertain_attempts", None)
                                repair_requests = getattr(provider, "transport_attempts", 0) or (
                                    1 + (repair_uncertain if repair_uncertain is not None else 0))
                                repair_billable = (max(0, repair_requests - 1)
                                                   if repair_uncertain is None else repair_uncertain)
                                ledger.settle(repair_reservation,
                                              repair_cost + repair_maximum * repair_billable)
                            repair_workdir = directory / f"task-{index}-{repeat}-repair"
                            repair_grade, repair_grading_error = _grade_response(
                                task, repair_response.text, repair_workdir, v2)
                            repair["response"] = asdict(repair_response)
                            repair["grade"] = repair_grade
                            repair["cost_usd"] = repair_cost
                            if v2:
                                latency = getattr(provider, "response_latency_seconds", None)
                                repair["response"].update(
                                    response_latency_seconds=(repair_response.latency_seconds
                                                              if latency is None else latency),
                                    transport_attempts=repair_requests,
                                    backoff_seconds=getattr(provider, "backoff_seconds", 0))
                            if repair_grading_error is not None:
                                repair["grading_error"] = repair_grading_error
                                if (repair_grade.get("status") == "not_run"
                                        or repair_grading_error.get("stop_run")):
                                    grading_stop = "Grading stopped: " + repair_grade["detail"]
                        except (ProviderUnavailable, ProviderInputError, SpendLimitExceeded) as error:
                            repair["error"] = str(error)
                            if isinstance(error, SpendLimitExceeded):
                                budget_error = str(error)
                attempt["repair"] = repair
                if result.get("status") == "not_run" or grading_error and grading_error.get("stop_run"):
                    attempt.update(status="not_run", error=result["detail"])
                    with (directory / "not-run.jsonl").open("a") as skipped:
                        skipped.write(json.dumps(attempt) + "\n")
                    grading_stop = "Grading stopped: " + result["detail"]
                    continue
                stream.write(json.dumps(attempt) + "\n")
                stream.flush()
    return directory, score_run(directory)
