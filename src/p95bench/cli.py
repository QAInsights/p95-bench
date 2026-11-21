import argparse
import json
import sys
from pathlib import Path
from urllib.error import URLError

from p95bench.packs import PACKS, pack_config
from p95bench.providers import PROVIDERS, get_provider
from p95bench.runner import run, score_run
from p95bench.tasks import TOOLS, load_tasks
from p95bench.worker import WorkerUnavailable


def main(argv=None):
    parser = argparse.ArgumentParser(prog="p95bench")
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run", aliases=["execute"])
    execute.add_argument("--provider", choices=PROVIDERS, required=True)
    execute.add_argument("--model", required=True)
    execute.add_argument("--tasks", type=Path)
    execute.add_argument("--out", type=Path, default=Path("results"))
    execute.add_argument("--repeats", type=int, default=1)
    execute.add_argument("--pack", choices=PACKS, default="p95-v1")
    execute.add_argument("--efficiency-budgets", type=Path)
    execute.add_argument("--repair-rounds", type=int, choices=(0, 1))
    execute.add_argument("--tool", choices=sorted(TOOLS), action="append")
    score = commands.add_parser("score")
    score.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "score":
            summary = score_run(args.directory)
        else:
            config = PROVIDERS[args.provider]
            if args.model not in config.prices:
                raise ValueError("Unknown model price; add a verified price before running")
            if args.repeats < 1:
                raise ValueError("repeats must be positive")
            categories = pack_config(args.pack)["weights"]
            tasks = load_tasks(args.tasks or (Path("tasks") if args.pack == "p95-v1" else
                                             Path("packs") / args.pack / "tasks"), categories)
            budgets = (json.loads(args.efficiency_budgets.read_text())
                       if args.efficiency_budgets else None)
            if args.tool:
                tasks = [task for task in tasks if task.tool in args.tool]
                if not tasks:
                    raise ValueError("No tasks match the selected tools")
            options = {}
            if args.repair_rounds is not None:
                options["repair_rounds"] = args.repair_rounds
            if args.pack != "p95-v1":
                options.update(pack_id=args.pack, efficiency_budgets=budgets)
            if args.pack == "p95-v1" and budgets is not None:
                raise ValueError("Efficiency budgets require a v2 pack")
            directory, summary = run(get_provider(args.provider), args.provider, args.model,
                                     None if args.provider == "gemini" else config.prices[args.model],
                                     tasks, args.out, args.repeats, **options)
            print(f"Run: {directory}")
        print(json.dumps(summary, indent=2))
        if summary.get("stop_reason"):
            raise ValueError("Run stopped: " + summary["stop_reason"])
    except (ValueError, OSError, URLError, WorkerUnavailable) as error:
        print(f"p95bench: {error}", file=sys.stderr)
        return 1
    return 0
