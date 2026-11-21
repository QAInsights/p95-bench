"""Validated YAML task loading; fixture paths stay under the task directory."""

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml

CATEGORIES = json.loads(files("p95bench").joinpath("category-weights.json").read_text())
TOOLS = {"jmeter", "k6", "gatling", "locust", "none"}


@dataclass
class Task:
    id: str
    category: str
    tool: str
    prompt: str
    grader: str
    params: dict
    fixtures: dict[str, str]
    scenario: str | None = None

    def user_prompt(self):
        return self.prompt + "".join(
            f"\n\nFixture {name}:\n{data}" for name, data in self.fixtures.items())


def load_tasks(root: Path, categories=None) -> list[Task]:
    categories = CATEGORIES if categories is None else categories
    tasks = []
    ids = set()
    for path in sorted(root.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        required = {"id", "category", "tool", "prompt", "grader"}
        if not isinstance(data, dict) or not required <= data.keys():
            raise ValueError(f"Missing task fields: {path}")
        if data["category"] not in categories or data["tool"] not in TOOLS:
            raise ValueError(f"Invalid category/tool: {path}")
        if any(not isinstance(data[k], str) or not data[k].strip() for k in required):
            raise ValueError(f"Task fields must be nonempty strings: {path}")
        scenario = data.get("scenario")
        if scenario is not None and (not isinstance(scenario, str) or not scenario.strip()):
            raise ValueError(f"Task scenario must be a nonempty string: {path}")
        if data["id"] in ids:
            raise ValueError(f"Duplicate task id: {data['id']}")
        ids.add(data["id"])
        fixtures = {}
        for name in data.get("fixtures", []):
            fixture = (path.parent / name).resolve()
            if not fixture.is_relative_to(path.parent.resolve()):
                raise ValueError(f"Fixture escapes task directory: {name}")
            fixtures[name] = fixture.read_text()
        tasks.append(Task(**{k: data[k] for k in required},
                          params=data.get("params", {}), fixtures=fixtures, scenario=scenario))
    if not tasks:
        raise ValueError(f"No YAML tasks found in {root}")
    return tasks
