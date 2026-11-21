"""Regenerate Matrix efficiency budgets using the documented v2 method."""

import argparse
import json
from pathlib import Path

import tiktoken

from p95bench.analysis_v2 import analysis_reference
from p95bench.packs import pack_config
from p95bench.reference_plans import reference
from p95bench.tasks import load_tasks

LANGUAGES = {
    "jmeter": "xml",
    "k6": "javascript",
    "locust": "python",
    "gatling": "java",
}


def build_budgets(tasks):
    encoding = tiktoken.get_encoding("o200k_base")
    budgets = {}
    for task in tasks:
        if task.grader == "v2_analysis":
            answer = json.dumps(analysis_reference(task.params, task.fixtures))
            latency = 90
        else:
            solution = reference(task.tool, task.params["contract"])
            answer = f"```{LANGUAGES[task.tool]}\n{solution}\n```"
            latency = 180
        budgets[task.id] = {
            "latency_seconds": latency,
            "tokens": 3 * len(encoding.encode(answer)),
        }
    return budgets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("packs/p95-matrix/efficiency-budgets.json"))
    args = parser.parse_args()
    tasks = load_tasks(
        Path("packs/p95-matrix/tasks"), pack_config("p95-matrix")["weights"])
    args.output.write_text(json.dumps(build_budgets(tasks), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
