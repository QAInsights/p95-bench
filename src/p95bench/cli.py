import argparse
import json
import sys
from pathlib import Path
from urllib.error import URLError

from p95bench.providers import PROVIDERS, get_provider
from p95bench.runner import run, score_run
from p95bench.tasks import load_tasks


def main(argv=None):
    parser = argparse.ArgumentParser(prog="p95bench")
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run")
    execute.add_argument("--provider", choices=PROVIDERS, required=True)
    execute.add_argument("--model", required=True)
    execute.add_argument("--tasks", type=Path, default=Path("tasks"))
    execute.add_argument("--out", type=Path, default=Path("results"))
    execute.add_argument("--repeats", type=int, default=1)
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
            tasks = load_tasks(args.tasks)
            directory, summary = run(get_provider(args.provider), args.provider, args.model,
                                     config.prices[args.model], tasks, args.out, args.repeats)
            print(f"Run: {directory}")
        print(json.dumps(summary, indent=2))
    except (ValueError, OSError, URLError) as error:
        print(f"p95bench: {error}", file=sys.stderr)
        return 1
    return 0
