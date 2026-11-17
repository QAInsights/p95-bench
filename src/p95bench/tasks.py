"""Validated YAML task loading; fixture paths stay under the task directory."""

from dataclasses import dataclass
from pathlib import Path

import yaml

CATEGORIES = {"authoring": 25, "correlation": 15, "workload": 15, "analysis": 15,
              "repair": 15, "migration": 10, "concepts": 5}
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

    def user_prompt(self):
        return self.prompt + "".join(
            f"\n\nFixture {name}:\n{data}" for name, data in self.fixtures.items())


def load_tasks(root: Path) -> list[Task]:
    tasks = []
    ids = set()
    for path in sorted(root.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        required = {"id", "category", "tool", "prompt", "grader"}
        if not isinstance(data, dict) or not required <= data.keys():
            raise ValueError(f"Missing task fields: {path}")
        if data["category"] not in CATEGORIES or data["tool"] not in TOOLS:
            raise ValueError(f"Invalid category/tool: {path}")
        if any(not isinstance(data[k], str) or not data[k].strip() for k in required):
            raise ValueError(f"Task fields must be nonempty strings: {path}")
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
                          params=data.get("params", {}), fixtures=fixtures))
    if not tasks:
        raise ValueError(f"No YAML tasks found in {root}")
    return tasks
