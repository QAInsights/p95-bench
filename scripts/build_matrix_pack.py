"""Generate the Matrix correlation and workload task variants."""

import argparse
import re
from copy import deepcopy
from pathlib import Path

import yaml

TOOLS = ("jmeter", "k6", "locust", "gatling")
CATEGORIES = ("correlation", "workload")
BUSINESS_FAILURE = re.compile(
    r"500 is unexpected\. /v2/health is not business work\..*?Keep checks truthful\.", re.DOTALL
)
TOOL_RULES = re.compile(
    r"Native results and server audit are authoritative; stdout is not evidence\."
    r".*?No eval/Groovy/BeanShell/JavaScript/JEXL\.",
    re.DOTALL,
)
HEADERS = {
    "k6": (
        "Return one complete k6 1.0.0 JS artifact, no explanation.",
        "Target only http://127.0.0.1:8000 (__ENV.BASE_URL).",
    ),
    "jmeter": (
        "Return one complete JMeter 5.6.3 JMX artifact, no explanation.",
        "Target only http://127.0.0.1:8000 (host/port properties or literals).",
    ),
    "locust": (
        "Return one complete Locust 2.47 Python locustfile, no explanation.",
        'Target only http://127.0.0.1:8000 (os.environ["BASE_URL"] is the host).',
    ),
    "gatling": (
        "Return one complete Gatling 3.15 Java DSL simulation, no explanation: "
        + "public class ModelSimulation extends Simulation, default package.",
        "Target only http://127.0.0.1:8000 (System.getenv(\"BASE_URL\")).",
    ),
}
GENERIC_NATIVE = "Native results and server audit are authoritative; stdout is not evidence."
TOOL_RULES_BY_TOOL = {
    "k6": GENERIC_NATIVE + " k6 handleSummary is disabled.",
    "locust": GENERIC_NATIVE + "\n"
        + "Define HttpUser classes and exactly one LoadTestShape; the harness runs Locust headless and stops when tick() returns None.\n"
        + "Each User instance is one virtual user. Use self.client only; mark failures with catch_response=True and response.failure().\n"
        + "Stop a user after its iterations with raise StopUser(). No subprocesses, threads, sockets, file writes, extra packages or custom event listeners.",
    "gatling": GENERIC_NATIVE + "\n"
        + "Java DSL only (io.gatling.javaapi.*); the harness compiles ModelSimulation.java with javac and runs it without reports.\n"
        + "Each Gatling virtual user is one user. Use http(...).check(...) for assertions; a failed check marks the request failed.\n"
        + "No threads, files, sockets, reflection, System.exit or extra libraries.",
}
NON_K6_BUSINESS_FAILURE = (
    "500 is unexpected. /v2/health is not business work.\n"
    + "Mark unexpected /v2/work failures as failed samples; expected expiry and scheduled 429 responses are not failures.\n"
    + "Every raw /v2/work response is one native sample. Keep assertions truthful."
)
VU_WORD = re.compile(r"\bVUs?\b")


def _once(pattern, text, replacement, section):
    result, count = pattern.subn(replacement, text)
    if count != 1:
        raise ValueError(f"Expected one {section} section, found {count}")
    return result


def protocol_for_tool(source, tool):
    if tool not in TOOLS:
        raise ValueError(f"Unknown tool: {tool}")
    lines = source.splitlines(keepends=True)
    if (len(lines) < 4 or lines[0] != "# Public shop protocol\n"
            or not lines[1].startswith("Return one complete ")
            or not lines[2].startswith("Target only ")):
        raise ValueError("Unexpected v2 protocol header")
    lines[1:3] = [line + "\n" for line in HEADERS[tool]]
    text = "".join(lines)

    business = BUSINESS_FAILURE.search(text)
    if business is None:
        raise ValueError("Missing v2 business_failure protocol section")
    if tool != "k6":
        text = _once(BUSINESS_FAILURE, text, NON_K6_BUSINESS_FAILURE,
                     "business_failure")

    rules = TOOL_RULES.search(text)
    if rules is None:
        raise ValueError("Missing v2 native tool rules")
    jmx_start = rules.group().find("JMX declarative components only:")
    jmx_end = rules.group().find("No eval/Groovy/BeanShell/JavaScript/JEXL.") + len(
        "No eval/Groovy/BeanShell/JavaScript/JEXL."
    )
    jmeter_rules = "\n".join((
        GENERIC_NATIVE,
        rules.group()[jmx_start:jmx_end],
    ))
    replacement = jmeter_rules if tool == "jmeter" else TOOL_RULES_BY_TOOL.get(tool)
    text = _once(TOOL_RULES, text, replacement, "native tool rules")
    return text


def _scenario_id(task_id):
    match = re.fullmatch(r"v2-([cw]\d+)-(.+)", task_id)
    if match is None:
        raise ValueError(f"Unexpected Phase A task id: {task_id}")
    code, slug = match.groups()
    return f"mx-{code}-{slug}"


def _copy_fixture(source_dir, target_dir, name):
    source = (source_dir / name).resolve()
    target = (target_dir / name).resolve()
    if not source.is_relative_to(source_dir.resolve()):
        raise ValueError(f"Fixture escapes source category: {name}")
    if not target.is_relative_to(target_dir.resolve()):
        raise ValueError(f"Fixture escapes output category: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def build_matrix_pack(source_pack, target_pack):
    source_pack, target_pack = Path(source_pack), Path(target_pack)
    output_tasks = target_pack / "tasks"
    scenario_count = 0
    for category in CATEGORIES:
        source_dir = source_pack / "tasks" / category
        output_dir = output_tasks / category
        output_dir.mkdir(parents=True, exist_ok=True)
        protocol = (source_dir / "protocol.md").read_text()
        for tool in TOOLS:
            (output_dir / f"protocol-{tool}.md").write_text(protocol_for_tool(protocol, tool))

        source_tasks = [
            yaml.safe_load(path.read_text())
            for path in sorted(source_dir.glob("*.yaml"))
        ]
        for original in source_tasks:
            if original["category"] != category or original["grader"] != "v2_exec":
                continue
            scenario = _scenario_id(original["id"])
            scenario_count += 1
            for tool in TOOLS:
                task = deepcopy(original)
                task["id"] = f"{scenario}-{tool}"
                task["scenario"] = scenario
                task["tool"] = tool
                task["params"]["tool"] = tool
                task["prompt"] = task["prompt"] if tool == "k6" else VU_WORD.sub(
                    lambda match: "virtual users" if match.group() == "VUs" else "virtual user",
                    task["prompt"],
                )
                fixtures = []
                for name in task.get("fixtures", []):
                    if name == "protocol.md":
                        name = f"protocol-{tool}.md"
                    else:
                        _copy_fixture(source_dir, output_dir, name)
                    fixtures.append(name)
                task["fixtures"] = fixtures
                target = output_dir / f"{task['id']}.yaml"
                target.write_text(yaml.safe_dump(task, sort_keys=False, allow_unicode=True))

    if scenario_count != 18:
        raise ValueError(f"Expected 18 Phase A scenarios, found {scenario_count}")
    for tool in TOOLS:
        placeholder = target_pack / "overlays" / tool / ".gitkeep"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        placeholder.write_text("")


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=root / "packs/p95-v2.0.0")
    parser.add_argument("--output", type=Path, default=root / "packs/p95-matrix")
    args = parser.parse_args(argv)
    build_matrix_pack(args.source, args.output)


if __name__ == "__main__":
    main()
