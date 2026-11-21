"""Versioned scoring contracts; no task content lives here."""

import hashlib
import json
import struct
from collections import defaultdict
from importlib.resources import files

from p95bench.grades import CheckpointGrade, finite
from p95bench.tasks import CATEGORIES

PACKS = json.loads(files("p95bench").joinpath("packs.json").read_text())


def pack_config(pack_id):
    if pack_id not in PACKS:
        raise ValueError(f"Unknown pack: {pack_id}")
    config = dict(PACKS[pack_id])
    config["weights"] = dict(config.get("weights", CATEGORIES))
    return config


def task_digest(task):
    task_content = {"tool": task.tool, "grader": task.grader, "prompt": task.prompt,
                    "params": task.params, "fixtures": task.fixtures}
    if task.scenario is not None:
        task_content["scenario"] = task.scenario
    return digest(task_content)


def snapshot(pack_id, tasks, repeats=1, budgets=None):
    config = pack_config(pack_id)
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    inventory = []
    for task in tasks:
        inventory_item = {"id": task.id, "category": task.category}
        if task.scenario is not None:
            inventory_item["scenario"] = task.scenario
        inventory_item["task_digest"] = task_digest(task)
        inventory.append(inventory_item)
    inventory.sort(key=lambda item: item["id"])
    if ((not inventory and config["grade_schema_version"] == 2)
            or len({t["id"] for t in inventory}) != len(inventory)):
        raise ValueError("Pack requires nonempty, unique tasks")
    if any(t["category"] not in config["weights"] for t in inventory):
        raise ValueError("Task category is not in the selected pack")
    budgets = budgets or {}
    if config["grade_schema_version"] == 2:
        if not isinstance(budgets, dict) or set(budgets) != {t.id for t in tasks}:
            raise ValueError("Provide frozen efficiency budgets for every task")
        for values in budgets.values():
            if (not isinstance(values, dict)
                    or not finite(values.get("tokens"), .001)
                    or not finite(values.get("latency_seconds"), .001)):
                raise ValueError("Efficiency budgets must be finite and positive")
    result = {"pack_id": pack_id, **config, "tasks": inventory, "repeats": repeats,
              "efficiency_budgets": budgets, "visibility": "public"}
    return {**result, "digest": digest(result)}


def digest(value):
    def canonical(item):
        if item is None:
            return ["null"]
        if isinstance(item, bool):
            return ["boolean", item]
        if isinstance(item, (int, float)):
            if not finite(abs(item)):
                raise ValueError("Nonfinite digest number")
            return ["number", struct.pack(">d", item).hex()]
        if isinstance(item, str):
            return ["string", item]
        if isinstance(item, list):
            return ["array", [canonical(v) for v in item]]
        return ["object", [[k, canonical(v)] for k, v in sorted(item.items())]]

    return hashlib.sha256(json.dumps(canonical(value), ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def validate_snapshot(value):
    data = dict(value)
    saved = data.pop("digest", None)
    if saved != digest(data):
        raise ValueError("Pack snapshot digest mismatch")
    config = pack_config(data["pack_id"])
    if any(data.get(k) != v for k, v in config.items()):
        raise ValueError("Pack scoring contract mismatch")


def efficiency(quality, response, budget):
    if not finite(quality) or quality > 1:
        raise ValueError("Quality must be between zero and one")
    for name in ("input_tokens", "output_tokens", "response_latency_seconds"):
        if not finite(response.get(name)):
            raise ValueError("Missing or invalid efficiency telemetry")
    if (not finite(budget.get("tokens"), .001)
            or not finite(budget.get("latency_seconds"), .001)):
        raise ValueError("Efficiency budgets must be finite and positive")
    tokens = response["output_tokens"]
    latency = response["response_latency_seconds"]
    token_credit = max(0, min(1, (3 - tokens / budget["tokens"]) / 2))
    latency_credit = max(0, min(1, (3 - latency / budget["latency_seconds"]) / 2))
    return quality * (.5 * token_credit + .5 * latency_credit)


def summarize_v2(attempts, manifest):
    validate_snapshot(manifest)
    if len({(a["model"], a["provider"]) for a in attempts}) > 1:
        raise ValueError("Mixed model/provider run")
    weights = manifest["weights"]
    inventory = {t["id"]: t["category"] for t in manifest["tasks"]}
    repeats = manifest["repeats"]
    seen = set()
    task_quality, task_efficiency = defaultdict(float), defaultdict(float)
    after_quality, after_efficiency = defaultdict(float), defaultdict(float)
    repairs_triggered = repairs_succeeded = 0
    for attempt in attempts:
        key = (attempt["task_id"], attempt["repeat"])
        if (key in seen or key[0] not in inventory or not isinstance(key[1], int)
                or isinstance(key[1], bool) or not 0 <= key[1] < repeats):
            raise ValueError("Duplicate or unknown trial slot")
        seen.add(key)
        if (attempt.get("pack_id") != manifest["pack_id"]
                or attempt.get("pack_digest") != manifest["digest"]
                or attempt["category"] != inventory[key[0]]):
            raise ValueError("Mixed pack or task category")
        quality = CheckpointGrade.from_dict(attempt["grade"]).score
        task_quality[key[0]] += quality / repeats
        task_efficiency[key[0]] += efficiency(
            quality, attempt["response"], manifest["efficiency_budgets"][key[0]]) / repeats
        repair = attempt.get("repair") or {}
        triggered = repair.get("triggered") is True
        repairs_triggered += int(triggered)
        repair_grade = repair.get("grade")
        repair_response = repair.get("response")
        if triggered and isinstance(repair_grade, dict) and repair_grade.get("status") == "graded":
            after_grade = CheckpointGrade.from_dict(repair_grade).score
            repairs_succeeded += int(repair_grade["passed"])
        else:
            after_grade = quality
        if triggered and isinstance(repair_response, dict):
            combined_response = dict(attempt["response"])
            for field in ("input_tokens", "output_tokens", "cached_tokens", "latency_seconds"):
                combined_response[field] = (attempt["response"].get(field, 0)
                                            + (repair_response or {}).get(field, 0))
            combined_response["response_latency_seconds"] = (
                attempt["response"].get("response_latency_seconds",
                                        attempt["response"]["latency_seconds"])
                + (repair_response or {}).get(
                    "response_latency_seconds", (repair_response or {}).get("latency_seconds", 0)))
        else:
            combined_response = attempt["response"]
        after_quality[key[0]] += after_grade / repeats
        after_efficiency[key[0]] += efficiency(
            after_grade, combined_response, manifest["efficiency_budgets"][key[0]]) / repeats
    categories, efficiencies = defaultdict(list), defaultdict(list)
    after_categories, after_efficiencies = defaultdict(list), defaultdict(list)
    for task_id, category in inventory.items():
        categories[category].append(task_quality[task_id])
        efficiencies[category].append(task_efficiency[task_id])
        after_categories[category].append(after_quality[task_id])
        after_efficiencies[category].append(after_efficiency[task_id])
    scores = {k: 100 * sum(categories[k]) / len(categories[k]) if categories[k] else 0
              for k in weights}
    quality = sum(weights[k] * scores[k] for k in weights) / 100
    efficient = sum(weights[k] * (sum(efficiencies[k]) / len(efficiencies[k])
                                 if efficiencies[k] else 0) for k in weights)
    after_scores = {k: 100 * sum(after_categories[k]) / len(after_categories[k])
                    if after_categories[k] else 0 for k in weights}
    after_quality_score = sum(weights[k] * after_scores[k] for k in weights) / 100
    after_efficient = sum(weights[k] * (sum(after_efficiencies[k]) / len(after_efficiencies[k])
                                        if after_efficiencies[k] else 0) for k in weights)
    weight = manifest["efficiency_weight"]
    expected = len(inventory) * repeats
    return {"pack_id": manifest["pack_id"], "pack_digest": manifest["digest"],
            "overall_score": (1 - weight) * quality + weight * efficient,
            "quality_score": quality, "efficiency_score": efficient,
            "category_scores": scores, "attempts": len(attempts), "expected_attempts": expected,
            "repeats": repeats,
            "complete": len(seen) == expected and set(inventory.values()) == set(weights),
            "passed": sum(a["grade"]["passed"] for a in attempts),
            "after_repair": {
                "overall_score": (1 - weight) * after_quality_score + weight * after_efficient,
                "quality_score": after_quality_score,
                "efficiency_score": after_efficient,
                "category_scores": after_scores,
            },
            "repairs_triggered": repairs_triggered,
            "repairs_succeeded": repairs_succeeded,
            "estimated_cost_usd": sum(a["cost_usd"] + (a.get("repair") or {}).get("cost_usd", 0)
                                      for a in attempts),
            "input_tokens": sum(a["response"]["input_tokens"]
                                + ((a.get("repair") or {}).get("response") or {}).get("input_tokens", 0)
                                for a in attempts),
            "output_tokens": sum(a["response"]["output_tokens"]
                                 + ((a.get("repair") or {}).get("response") or {}).get("output_tokens", 0)
                                 for a in attempts),
            "latency_seconds": sum(a["response"]["latency_seconds"]
                                   + ((a.get("repair") or {}).get("response") or {}).get("latency_seconds", 0)
                                   for a in attempts)}
