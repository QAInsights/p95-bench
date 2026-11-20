"""Generate tool-specific broken artifacts for the Matrix repair scenarios."""

import argparse
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from p95bench.reference_plans import reference, sampler

TOOLS = ("jmeter", "k6", "locust", "gatling")
EXTENSIONS = {"jmeter": "jmx", "k6": "js", "locust": "py", "gatling": "java"}


def _replace_once(text, old, new, description):
    if text.count(old) != 1:
        raise ValueError(f"Expected one {description}, found {text.count(old)}")
    return text.replace(old, new, 1)


def _jmeter_tree(text):
    return ET.fromstring(text)


def _parent_map(root):
    return {child: parent for parent in root.iter() for child in parent}


def _remove_component(element, parents):
    parent = parents[element]
    siblings = list(parent)
    index = siblings.index(element)
    del parent[index:index + 2]


def _paired_tree(element, parents):
    parent = parents[element]
    children = list(parent)
    return children[children.index(element) + 1]


def _property(element, name):
    item = next((child for child in element
                 if child.tag in ("stringProp", "boolProp", "intProp")
                 and child.get("name") == name), None)
    if item is None:
        raise ValueError(f"Missing JMeter property {name}")
    return item


def _jmeter_text(root):
    return ET.tostring(root, encoding="unicode")


def _r01(tool, source):
    if tool == "k6":
        source = _replace_once(
            source,
            "  let r=raw('/v2/login-form');",
            "  const earlier=raw('/v2/health');\n  let r=raw('/v2/login-form');",
            "R01 k6 earlier response",
        )
        source = _replace_once(source, "const token=r.html()", "const token=earlier.html()",
                               "R01 k6 token response")
        source = _replace_once(
            source,
            "  const token=earlier.html().find('input[name=\"csrf\"]').first().attr('value');",
            "  const token=earlier.html().find('input[name=\"csrf\"]').first().attr('value');\n"
            "  csrf=token;",
            "R01 k6 token assignment",
        )
        return _replace_once(
            source,
            "  r=raw('/v2/login','POST',{username:row.username,password:row.password,csrf:token});",
            "  r=raw('/v2/login','POST',{username:row.username,password:row.password,csrf:token});\n"
            "  csrf=token;",
            "R01 k6 stale token use",
        )
    if tool == "locust":
        source = _replace_once(source, "form, _ = self.raw('/v2/login-form')",
                               "earlier, _ = self.raw('/v2/health')\n"
                               "        _, _ = self.raw('/v2/login-form')",
                               "R01 Locust earlier response")
        return _replace_once(source, "form.text, re.IGNORECASE)",
                             "earlier.text, re.IGNORECASE)",
                             "R01 Locust response object")
    if tool == "gatling":
        start = source.index("    private static ChainBuilder login()")
        end = source.index("    private static ChainBuilder challenge()", start)
        login = source[start:end]
        login = _replace_once(
            login,
            '.saveAs("formCsrf")))',
            '.saveAs("formCsrf"), jsonPath("$.csrf").optional().saveAs("csrf")))',
            "R01 Gatling earlier-request extractor",
        )
        login = _replace_once(
            login,
            'status().in(200, 302), jsonPath("$.csrf").optional().saveAs("csrf"),',
            'status().in(200, 302),',
            "R01 Gatling login response extractor",
        )
        login = _replace_once(
            login,
            'jsonPath("$.grant_id").optional().saveAs("grant"),\n'
            '                        jsonPath("$.csrf").optional().saveAs("csrf")',
            'jsonPath("$.grant_id").optional().saveAs("grant")',
            "R01 Gatling redirect extractor",
        )
        return source[:start] + login + source[end:]
    raise ValueError(f"Unsupported R01 port: {tool}")


def _r02(tool, source):
    if tool == "jmeter":
        root = _jmeter_tree(source)
        parents = _parent_map(root)
        for manager in root.iter("CookieManager"):
            _property(manager, "CookieManager.clearEachIteration").text = "false"
        for extractor in list(root.iter("RegexExtractor")):
            if (extractor.findtext("stringProp[@name='RegexExtractor.refname']") in ("cart", "hasCart")
                    and extractor.findtext("stringProp[@name='RegexExtractor.regex']") == "$^"):
                _remove_component(extractor, parents)
        cart = next(sample for sample in root.iter("HTTPSamplerProxy")
                    if _property(sample, "HTTPSampler.path").text == "/v2/carts"
                    and _property(sample, "HTTPSampler.method").text == "POST")
        cart_tree = _paired_tree(cart, parents)
        parent = parents[cart]
        siblings = list(parent)
        index = siblings.index(cart)
        controller = ET.Element(
            "IfController", testname="IfController", testclass="IfController",
            guiclass="IfControllerPanel", enabled="true",
        )
        ET.SubElement(controller, "stringProp", name="IfController.condition").text = "!${hasCart}"
        ET.SubElement(controller, "boolProp", name="IfController.evaluateAll").text = "false"
        ET.SubElement(controller, "boolProp", name="IfController.useExpression").text = "true"
        controller_tree = ET.Element("hashTree")
        parent.remove(cart)
        parent.remove(cart_tree)
        parent.insert(index, controller)
        parent.insert(index + 1, controller_tree)
        controller_tree.extend((cart, cart_tree))
        return _jmeter_text(root)
    if tool == "locust":
        flow_start = source.index("    def flow(self):")
        flow = _replace_once(source[flow_start:], "        self.client.cookies.clear()\n", "",
                             "R02 Locust cookie jar")
        flow = _replace_once(flow, "        self.cart = ''\n", "",
                             "R02 Locust cart reset")
        source = source[:flow_start] + flow
        source = _replace_once(
            source,
            "        response, body = self.request('/v2/carts', 'POST',\n"
            "            {'product_id': product['id'], 'variant_id': variant['id'], 'grant_id': self.grant})\n"
            "        self.cart = body.get('id', '')\n"
            "        self.etag = response.headers.get('ETag', '')",
            "        if not self.cart:\n"
            "            response, body = self.request('/v2/carts', 'POST',\n"
            "                {'product_id': product['id'], 'variant_id': variant['id'], 'grant_id': self.grant})\n"
            "            self.cart = body.get('id', '')\n"
            "            self.etag = response.headers.get('ETag', '')",
            "R02 Locust cart reuse",
        )
        return source
    if tool == "gatling":
        source = _replace_once(
            source,
            "        ChainBuilder chain = exec(flushCookieJar()).exec(login());",
            "        ChainBuilder chain = login();",
            "R02 Gatling cookie jar",
        )
        source = _replace_once(
            source,
            '.remove("product").remove("variant").remove("nextPage").remove("cart").remove("etag")',
            '.remove("product").remove("variant").remove("nextPage").remove("etag")',
            "R02 Gatling cart reset",
        )
        start = source.index('        chain = chain\n            .exec(http("cart").post("/v2/carts")')
        end = source.index("\n\n        if (MODE.equals(\"expiry\")", start)
        block = source[start:end]
        block = _replace_once(
            block, '.exec(http("cart").post("/v2/carts")',
            '.exec(doIf(session -> !session.contains("cart")).then(\n'
            '                exec(http("cart").post("/v2/carts")',
            "R02 Gatling cart creation",
        )
        if not block.endswith(")));"):
            raise ValueError("Unexpected Gatling cart chain terminator")
        block = block[:-4] + ")))));"
        return source[:start] + block + source[end:]
    raise ValueError(f"Unsupported R02 port: {tool}")


def _r04(tool, source):
    if tool == "jmeter":
        root = _jmeter_tree(source)
        parents = _parent_map(root)
        for controller in list(root.iter("IfController")):
            if controller.findtext("stringProp[@name='IfController.condition']") == "${recover}":
                _remove_component(controller, parents)
        order = next(sample for sample in root.iter("HTTPSamplerProxy")
                     if _property(sample, "HTTPSampler.path").text == "/v2/orders")
        order_tree = _paired_tree(order, parents)
        assertion = ET.Element("ResponseAssertion", {
            "testname": "ResponseAssertion-recovery", "testclass": "ResponseAssertion",
            "guiclass": "AssertionGui", "enabled": "true",
        })
        ET.SubElement(assertion, "stringProp", name="Assertion.test_field").text = "Assertion.response_code"
        ET.SubElement(assertion, "stringProp", name="Assertion.test_type").text = "2"
        ET.SubElement(assertion, "boolProp", name="Assertion.assume_success").text = "true"
        strings = ET.SubElement(assertion, "collectionProp", name="Asserion.test_strings")
        ET.SubElement(strings, "stringProp", name="0").text = "201"
        ET.SubElement(strings, "stringProp", name="1").text = "401"
        order_tree.extend((assertion, ET.Element("hashTree")))
        return _jmeter_text(root)
    if tool == "locust":
        source = _replace_once(
            source,
            "            allowed = (response.status_code < 400\n"
            "                       or response.status_code == 401 and error in ('expired', 'session')\n"
            "                       or response.status_code == 412 and error in ('etag', 'etag_conflict')\n"
            "                       or response.status_code == 429 and path.split('?', 1)[0] == '/v2/work')",
            "            allowed = (response.status_code < 400 or response.status_code == 401)",
            "R04 Locust status rule",
        )
        source = _replace_once(
            source, "            error = body.get('error')\n", "",
            "R04 Locust unused error value",
        )
        return _replace_once(
            source,
            "            if response.status_code == 401 and body.get('error') in ('expired', 'session'):\n"
            "                self.recover()\n"
            "                continue\n",
            "",
            "R04 Locust renewal branch",
        )
    if tool == "gatling":
        source = source.replace(
            'session -> session.getInt("catalogStatus") == 401',
            "session -> false",
        )
        source = source.replace(
            'session -> session.getInt("expiryStatus") == 401',
            "session -> false",
        )
        return source.replace("status().in(200, 401)", "status().in(200, 201, 401)")
    raise ValueError(f"Unsupported R04 port: {tool}")


def _r05(tool, source):
    if tool == "k6":
        return _replace_once(
            source, "r=>r.status===201 && body(r).accepted===true", "r=>r.status===200",
            "R05 k6 status-only assertion",
        )
    if tool == "locust":
        return _replace_once(
            source,
            "                    or path.split('?', 1)[0] == '/v2/orders' and method == 'POST'\n"
            "                    and response.status_code < 400\n"
            "                    and body.get('accepted') is not True",
            "",
            "R05 Locust business assertion",
        )
    if tool == "gatling":
        return _replace_once(
            source, ".check(status().is(201), jsonPath(\"$.accepted\").is(\"true\"),",
            ".check(status().is(200),",
            "R05 Gatling status-only assertion",
        )
    raise ValueError(f"Unsupported R05 port: {tool}")


def _r06(tool, source):
    if tool == "jmeter":
        root = _jmeter_tree(source)
        parents = _parent_map(root)
        checkout = next(sample for sample in root.iter("HTTPSamplerProxy")
                        if _property(sample, "HTTPSampler.path").text == "/v2/orders")
        checkout.set("testname", "commit")
        parent = parents[checkout]
        checkout_tree = _paired_tree(checkout, parents)
        duration = next(item for item in checkout_tree.iter("DurationAssertion"))
        _remove_component(duration, _parent_map(root))
        health_parent = ET.Element("hashTree")
        sampler(health_parent, "/v2/health", name="checkout", duration_assertion=250)
        parent.extend(list(health_parent))
        return _jmeter_text(root)
    if tool == "locust":
        source = _replace_once(
            source,
            "        self.request('/v2/orders', 'POST', body,\n"
            "                     {'Idempotency-Key': self.cart, 'If-Match': self.etag},\n"
            "                     'checkout' if MODE == 'threshold' else None)",
            "        if MODE == 'threshold':\n"
            "            self.request('/v2/health', name='checkout')\n"
            "        self.request('/v2/orders', 'POST', body,\n"
            "                     {'Idempotency-Key': self.cart, 'If-Match': self.etag}, None)",
            "R06 Locust checkout label",
        )
        return source
    if tool == "gatling":
        source = _replace_once(
            source,
            "        return chain.exec(doIf(session -> !MODE.equals(\"reservation\")\n",
            "        if (MODE.equals(\"threshold\")) {\n"
            "            chain = chain.exec(http(\"checkout\").get(\"/v2/health\")\n"
            "                .check(status().is(200)));\n"
            "        }\n"
            "        return chain.exec(doIf(session -> !MODE.equals(\"reservation\")\n",
            "R06 Gatling health request",
        )
        return _replace_once(
            source, 'http(MODE.equals("threshold") ? "checkout" : "commit")',
            'http("commit")',
            "R06 Gatling checkout label",
        )
    raise ValueError(f"Unsupported R06 port: {tool}")


def _r07(tool, source):
    if tool == "k6":
        return _replace_once(
            source,
            "const b=body(r); if(b.csrf) csrf=b.csrf;",
            "const b=body(r); if(b.csrf && !(cfg.mode==='expiry' && path==='/v2/session/renew')) csrf=b.csrf;",
            "R07 k6 stale CSRF",
        )
    if tool == "locust":
        source = _replace_once(
            source,
            "        self.iteration = 0\n",
            "        self.iteration = 0\n"
            "        self.login()\n",
            "R07 Locust on-start login",
        )
        source = _replace_once(source, "            if body.get('csrf'):\n                self.csrf = body['csrf']\n",
                               "", "R07 Locust response CSRF refresh")
        login_start = source.index("    def login(self):")
        login_end = source.index("    def recover(self):", login_start)
        login = _replace_once(
            source[login_start:login_end],
            "        self.grant = body.get('grant_id', '')\n",
            "        self.csrf = body.get('csrf', self.csrf)\n"
            "        self.grant = body.get('grant_id', '')\n",
            "R07 Locust initial CSRF",
        )
        source = source[:login_start] + login + source[login_end:]
        return _replace_once(source, "        self.login()\n        if MODE == 'ownership':",
                             "        if MODE == 'ownership':",
                             "R07 Locust per-iteration login")
    if tool == "gatling":
        start = source.index("    private static ChainBuilder flow()")
        end = source.index("\n\n    public ModelSimulation()", start)
        flow = source[start:end]
        flow = _replace_once(
            flow,
            "        ChainBuilder chain = exec(flushCookieJar()).exec(login());",
            "        ChainBuilder chain = exec(session -> session);",
            "R07 Gatling per-flow login",
        )
        flow, count = flow.replace(
            'jsonPath("$.csrf").optional().saveAs("csrf")',
            'jsonPath("$.csrf").optional().saveAs("staleCsrf")',
        ), flow.count('jsonPath("$.csrf").optional().saveAs("csrf")')
        if count < 1:
            raise ValueError("Missing R07 Gatling CSRF updates")
        source = source[:start] + flow + source[end:]
        return _replace_once(
            source,
            'setUp(scenario("matrix").exec(repeat(3).on(flow())).injectOpen(atOnceUsers(2)))',
            'setUp(scenario("matrix").exec(login()).exec(repeat(3).on(flow()))'
            '.injectOpen(atOnceUsers(2)))',
            "R07 Gatling login scope",
        )
    raise ValueError(f"Unsupported R07 port: {tool}")


def _r08(tool, source):
    if tool == "jmeter":
        root = _jmeter_tree(source)
        for group in root.iter("ThreadGroup"):
            _property(group, "ThreadGroup.num_threads").text = "1"
            group.find("boolProp[@name='ThreadGroup.scheduler']").text = "true"
            duration = group.find("stringProp[@name='ThreadGroup.duration']")
            if duration is not None:
                duration.text = "38"
            controller = group.find("elementProp[@name='ThreadGroup.main_controller']")
            controller.find("boolProp[@name='LoopController.continue_forever']").text = "false"
            controller.find("stringProp[@name='LoopController.loops']").text = "2"
        return _jmeter_text(root)
    if tool == "locust":
        source = _replace_once(source, "DURATION = CFG.get('duration', 35)",
                               "DURATION = 10", "R08 Locust stop time")
        return _replace_once(
            source,
            "users = (CFG.get('max_vus', 64 if CFG.get('capacity') or CFG.get('pools') else 32)\n"
            "                 if CFG.get('workload') else CFG.get('users', 2))",
            "users = (1 if CFG.get('workload') else CFG.get('users', 2))",
            "R08 Locust user count",
        )
    if tool == "gatling":
        pattern = re.compile(
            r"constantUsersPerSec\((?P<rate>[\d.]+)\)\.during\(Duration\.ofMillis\((?P<span>\d+)\)\)"
        )
        match = pattern.search(source)
        if match is None:
            raise ValueError("Missing R08 Gatling injection step")
        span = int(match["span"])
        first_span = max(1, span - 6000)
        replacement = (
            f"constantUsersPerSec(1.0).during(Duration.ofMillis({first_span})), "
            f"constantUsersPerSec({match['rate']}).during(Duration.ofMillis(6000))"
        )
        return source[:match.start()] + replacement + source[match.end():]
    raise ValueError(f"Unsupported R08 port: {tool}")


def _r10(tool, source):
    if tool == "jmeter":
        root = _jmeter_tree(source)
        for dataset in root.iter("CSVDataSet"):
            _property(dataset, "filename").text = "/fixtures/account-1.csv"
            _property(dataset, "shareMode").text = "shareMode.all"
        return _jmeter_text(root)
    if tool == "locust":
        source = _replace_once(
            source,
            "ROWS = json.loads(open('/fixtures/accounts.json').read())",
            "with open('/fixtures/accounts.json', encoding='utf-8') as accounts_file:\n"
            "    ROWS = json.load(accounts_file)",
            "R10 Locust fixture load",
        )
        return _replace_once(
            source, "        self.row = ROWS[(number - 1) % len(ROWS)]",
            "        self.row = ROWS[0]",
            "R10 Locust shared account",
        )
    if tool == "gatling":
        return _replace_once(
            source,
            "            return rows.get((int) ((session.userId() - 1) % rows.size()));",
            "            return rows.get(0);",
            "R10 Gatling shared account",
        )
    raise ValueError(f"Unsupported R10 port: {tool}")


def _r11(tool, source):
    if tool == "k6":
        return _replace_once(
            source,
            "  const b=body(r); if(b.csrf) csrf=b.csrf;",
            "  sleep(1);\n  const b=body(r); if(b.csrf) csrf=b.csrf;",
            "R11 k6 per-request pacing",
        )
    if tool == "locust":
        return _replace_once(
            source,
            "            if body.get('csrf'):\n"
            "                self.csrf = body['csrf']\n"
            "            return response, body",
            "            if body.get('csrf'):\n"
            "                self.csrf = body['csrf']\n"
            "            sleep(1)\n"
            "            return response, body",
            "R11 Locust per-request pacing",
        )
    if tool == "gatling":
        pause = 'pause(Duration.ofMillis((long) (1000 / CFG.path("rate").asDouble(1))))'
        return source.replace("exec(http(", f"exec({pause}, http(")
    raise ValueError(f"Unsupported R11 port: {tool}")


def _r12(tool, source):
    if tool == "jmeter":
        root = _jmeter_tree(source)
        parents = _parent_map(root)
        for sample in root.iter("HTTPSamplerProxy"):
            if _property(sample, "HTTPSampler.path").text != "/v2/work":
                continue
            for assertion in _paired_tree(sample, parents).iter("ResponseAssertion"):
                strings = assertion.find("collectionProp[@name='Asserion.test_strings']")
                for item in list(strings):
                    strings.remove(item)
                ET.SubElement(strings, "stringProp", name="0").text = "^(?:200|500)$"
        return _jmeter_text(root)
    if tool == "locust":
        return _replace_once(
            source,
            "            error = body.get('error')\n"
            "            allowed = (response.status_code < 400\n"
            "                       or response.status_code == 401 and error in ('expired', 'session')\n"
            "                       or response.status_code == 412 and error in ('etag', 'etag_conflict')\n"
            "                       or response.status_code == 429 and path.split('?', 1)[0] == '/v2/work')",
            "            allowed = response.status_code < 400 or response.status_code == 500",
            "R12 Locust failure marking",
        )
    if tool == "gatling":
        return source.replace("status().in(200, 401, 429)", "status().in(200, 500)")
    raise ValueError(f"Unsupported R12 port: {tool}")


def _r13(tool, source):
    if tool == "k6":
        source = _replace_once(source, "let csrf = '', cart = '', grant = '';",
                               "let csrf = '', cart = '', grant = '', restartCheckout = false;",
                               "R13 k6 restart state")
        source = _replace_once(
            source,
            "  const b=body(r); if(b.csrf) csrf=b.csrf;",
            "  const b=body(r); if(cfg.mode==='expiry' && r.status===401 && b.error==='expired') restartCheckout=true;\n"
            "  if(b.csrf) csrf=b.csrf;",
            "R13 k6 expiry signal",
        )
        return _replace_once(
            source,
            "  else flow();\n}",
            "  else {\n"
            "    flow();\n"
            "    for(let n=0;n<1 && restartCheckout;n++){restartCheckout=false;flow();}\n"
            "  }\n}",
            "R13 k6 whole-checkout retry",
        )
    if tool == "locust":
        source = _replace_once(
            source,
            "        self.iteration = 0\n",
            "        self.iteration = 0\n"
            "        self.restart_checkout = False\n",
            "R13 Locust retry state",
        )
        source = _replace_once(
            source,
            "            error = body.get('error')\n",
            "            error = body.get('error')\n"
            "            if MODE == 'expiry' and response.status_code == 401 and error == 'expired':\n"
            "                self.restart_checkout = True\n",
            "R13 Locust expiry signal",
        )
        scenario_start = source.index("    def run_scenario(self):")
        scenario_end = source.index("\nclass MatrixShape", scenario_start)
        scenario = _replace_once(
            source[scenario_start:scenario_end],
            "        else:\n"
            "            self.flow()\n"
            "        self.iteration += 1",
            "        else:\n"
            "            self.flow()\n"
            "            if self.restart_checkout:\n"
            "                self.restart_checkout = False\n"
            "                self.flow()\n"
            "        self.iteration += 1",
            "R13 Locust whole-checkout retry",
        )
        return source[:scenario_start] + scenario + source[scenario_end:]
    if tool == "gatling":
        source = _replace_once(
            source,
            '.check(status().in(200, 401), status().saveAs("expiryStatus"))',
            '.check(status().is(200), status().saveAs("expiryStatus"))',
            "R13 Gatling expiry retry",
        )
        source, count = re.subn(
            r"repeat\((\d+)\)\.on\(flow\(\)\)",
            r"repeat(\1).on(tryMax(2).on(flow()))",
            source,
        )
        if count != 1:
            raise ValueError(f"Expected one R13 Gatling repeat, found {count}")
        return source
    raise ValueError(f"Unsupported R13 port: {tool}")


PORTS = {
    "r01": _r01, "r02": _r02, "r04": _r04, "r05": _r05, "r06": _r06,
    "r07": _r07, "r08": _r08, "r10": _r10, "r11": _r11, "r12": _r12, "r13": _r13,
}


def build_repair_overlays(source_pack, target_pack):
    source_pack, target_pack = Path(source_pack), Path(target_pack)
    source_dir = source_pack / "tasks" / "repair"
    for tool in TOOLS:
        overlay = target_pack / "overlays" / tool
        overlay.mkdir(parents=True, exist_ok=True)
        (overlay / ".gitkeep").write_bytes(b"")
    for task_path in sorted(source_dir.glob("v2-r*.yaml")):
        task = yaml.safe_load(task_path.read_text())
        code = task["id"].split("-", 2)[1]
        original_tool = task["tool"]
        original_fixture = next(name for name in task["fixtures"] if name.startswith("broken-r"))
        for tool in TOOLS:
            overlay = target_pack / "overlays" / tool
            overlay.mkdir(parents=True, exist_ok=True)
            destination = overlay / f"broken-{code}.{EXTENSIONS[tool]}"
            if tool == original_tool:
                shutil.copyfile(source_dir / original_fixture, destination)
                continue
            text = reference(tool, task["params"]["contract"])
            try:
                text = PORTS[code](tool, text)
            except (KeyError, ValueError) as error:
                raise ValueError(f"Unable to port {task['id']} to {tool}: {error}") from error
            destination.write_text(text)
    return target_pack


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=root / "packs/p95-v2.0.0")
    parser.add_argument("--output", type=Path, default=root / "packs/p95-matrix")
    args = parser.parse_args(argv)
    build_repair_overlays(args.source, args.output)


if __name__ == "__main__":
    main()
