"""Generate the Matrix Phase A and repair task variants."""

import argparse
import re
from copy import deepcopy
from pathlib import Path

import yaml

from p95bench.reference_plans import reference

CATEGORIES = ("correlation", "workload", "repair", "migration")
TOOLS = ("jmeter", "k6", "locust", "gatling")
EXPECTED_SCENARIOS = {"correlation": 10, "workload": 8, "repair": 11, "migration": 6}
TOOL_EXTENSIONS = {"jmeter": "jmx", "k6": "js", "locust": "py", "gatling": "java"}
REPAIR_PROMPTS = {
    ("r01", "k6"): "Fix a CSRF/ID value parsed from the wrong response; run two users for two iterations.",
    ("r01", "locust"): "Fix a CSRF/ID value read from the wrong response object; run two users for two iterations.",
    ("r01", "gatling"): "Fix a jsonPath check saved from the wrong request; run two users for two iterations.",
    ("r02", "jmeter"): "Fix cookie and cart variables that carry a completed cart into the next iteration.",
    ("r02", "locust"): "Fix session cookies and cart state that carry a completed cart into the next iteration.",
    ("r02", "gatling"): "Fix a cookie jar and cart session attribute that carry a completed cart into the next iteration.",
    ("r04", "jmeter"): "Repair an assertion that treats every 401 as success and never renews the session.",
    ("r04", "locust"): "Repair response handling that marks every 401 as success and never renews the session.",
    ("r04", "gatling"): "Repair status checks that accept every 401 as success and never renew the session.",
    ("r05", "k6"): "Fix an HTTP-200-only check that accepts a declined business transaction.",
    ("r05", "locust"): "Fix an HTTP-200-only success rule that accepts a declined business transaction.",
    ("r05", "gatling"): "Fix an HTTP-200-only check that accepts a declined business transaction.",
    ("r06", "jmeter"): "Fix a checkout latency assertion and label accidentally attached to fast health requests.",
    ("r06", "locust"): "Fix a checkout latency threshold whose request name is accidentally attached to fast health requests.",
    ("r06", "gatling"): "Fix a checkout latency assertion accidentally attached to fast health requests.",
    ("r07", "k6"): "Fix a CSRF token captured once outside the iteration and reused after a session renewal.",
    ("r07", "locust"): "Fix a CSRF token captured only in on_start and reused after a session renewal.",
    ("r07", "gatling"): "Fix a CSRF token saved once before the repeat loop and reused after a session renewal.",
    ("r08", "jmeter"): "Repair a conflicting loop count/duration and an under-provisioned thread pool for the target rate.",
    ("r08", "locust"): "Repair a conflicting stop time and an under-provisioned user count in the LoadTestShape for the target rate.",
    ("r08", "gatling"): "Repair conflicting injection steps and an under-provisioned user count for the target rate.",
    ("r10", "jmeter"): "Fix CSV credential sharing so users do not invalidate each other's exclusive sessions.",
    ("r10", "locust"): "Fix credential assignment so users do not invalidate each other's exclusive sessions.",
    ("r10", "gatling"): "Fix feeder credential reuse so users do not invalidate each other's exclusive sessions.",
    ("r11", "k6"): "Repair pacing applied after every HTTP request instead of once per business iteration.",
    ("r11", "locust"): "Repair a wait applied after every HTTP request instead of once per business iteration.",
    ("r11", "gatling"): "Repair pauses applied after every HTTP request instead of pace() once per business iteration.",
    ("r12", "jmeter"): "Fix failure marking that counts expected expiry and scheduled 429 as errors and accepts unexpected 500s.",
    ("r12", "locust"): "Fix failure marking that counts expected expiry and scheduled 429 as errors and accepts unexpected 500s.",
    ("r12", "gatling"): "Fix checks that count expected expiry and scheduled 429 as errors and accept unexpected 500s.",
    ("r13", "k6"): "Repair a retry loop that restarts checkout after expiry and duplicates business work.",
    ("r13", "locust"): "Repair a retry loop that restarts checkout after expiry and duplicates business work.",
    ("r13", "gatling"): "Repair a tryMax block that restarts checkout after expiry and duplicates business work.",
}
MIGRATION_PROMPTS = {
    ("m01", "locust"): "Convert a JMX cookie/CSRF shop plan to Locust without changing its semantics.",
    ("m01", "gatling"): "Convert a JMX cookie/CSRF shop plan to Gatling without changing its semantics.",
    ("m01", "jmeter"): "Convert a k6 cookie/CSRF shop script to JMeter without changing its semantics.",
    ("m03", "locust"): "Preserve a JMX flow that extracts a redirect location and follows a one-use token, in Locust.",
    ("m03", "gatling"): "Preserve a JMX flow that extracts a redirect location and follows a one-use token, in Gatling.",
    ("m03", "jmeter"): "Preserve a k6 flow that extracts a redirect location and follows a one-use token, in JMeter.",
    ("m04", "locust"): "Convert CSV-driven JMeter users and parameter substitution into deterministic Locust fixture use.",
    ("m04", "gatling"): "Convert CSV-driven JMeter users and parameter substitution into deterministic Gatling feeder use.",
    ("m04", "jmeter"): "Convert fixture-driven k6 users into deterministic JMeter CSV Data Set use.",
    ("m05", "locust"): "Convert a JMX plan's specified iteration pacing into an equivalent Locust workload.",
    ("m05", "gatling"): "Convert a JMX plan's specified iteration pacing into an equivalent Gatling workload.",
    ("m05", "jmeter"): "Convert a k6 script's specified iteration pacing into an equivalent JMeter workload.",
    ("m06", "locust"): "Translate sampler assertions and transaction-level error limits into Locust response checks and an exit-code rule.",
    ("m06", "gatling"): "Translate sampler assertions and transaction-level error limits into Gatling checks and assertions.",
    ("m06", "jmeter"): "Translate k6 checks and thresholds into JMeter assertions and transaction-level error limits.",
    ("m07", "locust"): "Migrate nested conditional/loop controllers with a bounded refresh branch to Locust.",
    ("m07", "gatling"): "Migrate nested conditional/loop controllers with a bounded refresh branch to Gatling.",
    ("m07", "jmeter"): "Migrate nested k6 conditionals/loops with a bounded refresh branch to JMeter controllers.",
}
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
    match = re.fullmatch(r"v2-([cwrm]\d+)-(.+)", task_id)
    if match is None:
        raise ValueError(f"Unexpected Phase A task id: {task_id}")
    code, slug = match.groups()
    return f"mx-{code}-{slug}"


def _text_for_tool(text, tool, scenario):
    if tool != "jmeter":
        text = text.replace("HTTP samplers", "HTTP requests")
        text = text.replace("per-thread", "per-virtual-user")
        text = text.replace("plan loads", "script loads")
        text = text.replace("crashing the plan", "crashing the script")
    else:
        text = text.replace("script loads", "plan loads")
    if scenario == "mx-w08-capacity-step" and tool != "k6":
        text = text.replace(
            "low dropped-iteration count with bounded preallocated/max VUs",
            "low dropped-start count with a bounded virtual-user pool",
        )
    if tool != "k6":
        text = VU_WORD.sub(
            lambda match: "virtual users" if match.group() == "VUs" else "virtual user",
            text,
        )
    return text


def _copy_fixture(source_dir, target_dir, name):
    source = (source_dir / name).resolve()
    target = (target_dir / name).resolve()
    if not source.is_relative_to(source_dir.resolve()):
        raise ValueError(f"Fixture escapes source category: {name}")
    if not target.is_relative_to(target_dir.resolve()):
        raise ValueError(f"Fixture escapes output category: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _copy_overlay(target_pack, target_dir, tool, name):
    source = (target_pack / "overlays" / tool / name).resolve()
    target = (target_dir / name).resolve()
    if not source.is_relative_to((target_pack / "overlays" / tool).resolve()):
        raise ValueError(f"Artifact escapes tool overlay: {name}")
    if not target.is_relative_to(target_dir.resolve()):
        raise ValueError(f"Artifact escapes output category: {name}")
    target.write_bytes(source.read_bytes())


def build_matrix_pack(source_pack, target_pack):
    source_pack, target_pack = Path(source_pack), Path(target_pack)
    output_tasks = target_pack / "tasks"
    scenario_counts = {category: 0 for category in CATEGORIES}
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
            scenario_counts[category] += 1
            for tool in TOOLS:
                task = deepcopy(original)
                task["id"] = f"{scenario}-{tool}"
                task["scenario"] = scenario
                task["tool"] = tool
                task["params"]["tool"] = tool
                if category == "migration" and tool != "k6":
                    code = scenario.split("-", 2)[1]
                    source_tool = "JMX" if tool in ("locust", "gatling") else "k6 script"
                    target_tool = {"jmeter": "JMeter", "locust": "Locust", "gatling": "Gatling"}[tool]
                    line_two = (
                        f"Translate the supplied JMX to {target_tool}."
                        if source_tool == "JMX"
                        else "Translate the supplied k6 script to JMeter."
                    )
                    body = "\n".join(task["prompt"].splitlines()[2:]).lstrip("\n")
                    task["prompt"] = (
                        MIGRATION_PROMPTS[(code, tool)] + "\n\n" + line_two + "\n\n" + body
                    )
                if category == "migration" and tool == "jmeter":
                    source_name = next(name for name in task["fixtures"] if name.startswith("source-m"))
                    source_js = Path(source_name).with_suffix(".js").name
                    overlay = target_pack / "overlays" / "jmeter" / source_js
                    overlay.parent.mkdir(parents=True, exist_ok=True)
                    overlay.write_text(reference("k6", task["params"]["contract"]))
                if category == "repair":
                    code = scenario.split("-", 2)[1]
                    prompt = REPAIR_PROMPTS.get((code, tool), task["prompt"].split("\n\n", 1)[0])
                    prompt_lines = task["prompt"].splitlines()
                    if len(prompt_lines) < 2 or prompt_lines[1] != "Repair the supplied artifact.":
                        raise ValueError(f"Missing repair instruction line in {original['id']}")
                    rest = "\n".join(prompt_lines[2:]).lstrip("\n")
                    task["prompt"] = prompt + "\n\nRepair the supplied artifact.\n\n" + rest
                    artifact = f"broken-{code}.{TOOL_EXTENSIONS[tool]}"
                    task["fixtures"] = [
                        artifact if name.startswith("broken-r") else name
                        for name in task.get("fixtures", [])
                    ]
                if category != "migration":
                    task["prompt"] = _text_for_tool(task["prompt"], tool, scenario)
                    for rubric_item in task["params"].get("rubric", []):
                        rubric_item["description"] = _text_for_tool(
                            rubric_item["description"], tool, scenario)
                fixtures = []
                for name in task.get("fixtures", []):
                    if name == "protocol.md":
                        name = f"protocol-{tool}.md"
                    elif category == "repair" and name.startswith("broken-r"):
                        _copy_overlay(target_pack, output_dir, tool, name)
                    elif category == "migration" and name.startswith("source-m") and tool == "jmeter":
                        name = Path(name).with_suffix(".js").name
                        _copy_overlay(target_pack, output_dir, tool, name)
                    else:
                        _copy_fixture(source_dir, output_dir, name)
                    fixtures.append(name)
                task["fixtures"] = fixtures
                target = output_dir / f"{task['id']}.yaml"
                target.write_text(yaml.safe_dump(task, sort_keys=False, allow_unicode=True))

    if scenario_counts != EXPECTED_SCENARIOS:
        raise ValueError(f"Unexpected Matrix scenario counts: {scenario_counts}")
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
