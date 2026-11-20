"""Trusted public reference generators, also used for capacity qualification."""

import json
import math
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from itertools import count

NAMES = ContextVar("reference_names")
LOGIN_USER = ContextVar("reference_user", default="bench-${__threadNum}")

K6 = r"""
import http from 'k6/http';
import {check, sleep} from 'k6';
import {Rate} from 'k6/metrics';
const cfg = CONFIG;
const rows = DATA;
const failure = new Rate('business_failure');
export const options = OPTIONS;
let csrf = '', cart = '', grant = '';
const base = __ENV.BASE_URL;
function body(r) { try { return r.json(); } catch (_) { return {}; } }
function raw(path, method='GET', data=null, extra={}) {
  const r = http.request(method, base + path, data===null ? null : JSON.stringify(data), {
    headers: Object.assign({'Content-Type':'application/json','X-CSRF-Token':csrf},extra),
    redirects:0, timeout:'3s'});
  const b=body(r); if(b.csrf) csrf=b.csrf;
  if(path==='/v2/work') failure.add(r.status>=400 && !(r.status===401 && b.error==='expired') && r.status!==429);
  return r;
}
function login(){
  let r=raw('/v2/login-form');
  const token=r.html().find('input[name="csrf"]').first().attr('value');
  const row=rows[(__VU-1)%rows.length];
  r=raw('/v2/login','POST',{username:row.username,password:row.password,csrf:token});
  if(r.status===302) {r=raw(r.headers.Location);grant=body(r).grant_id;}
  check(r,{'login':r=>r.status===200});
}
function request(path, method='GET', data=null, extra={}){
  let r=raw(path,method,data,extra);
  if(r.status===401 && ['expired','session'].indexOf(body(r).error)>=0){
    const renewed=raw('/v2/session/renew','POST',{});
    if(renewed.status!==200) login();
    if(cart) raw('/v2/carts/'+cart+'/recover','POST',{});
    r=raw(path,method,data,extra);
  }
  return r;
}
function work(override){
  if(cfg.workload || !csrf) login();
  const path=override||cfg.path||'/v2/work';
  let r=request(path,'POST',{});
  for(let n=0;n<4 && r.status===429;n++){
    sleep(Number(r.headers['Retry-After'])+.01);r=request(path,'POST',{});
  }
  check(r,{'operation or expected throttle':r=>(r.status===200 && body(r).ok===true) || (cfg.completion && r.status===429)});
}
function flow(){
  csrf='';cart='';grant='';login();
  if(cfg.mode==='challenge'){
    let r=request('/v2/challenge-form');const form=r.html().find('form');
    const token=form.find('input[name="challenge"]').first().attr('value');
    r=request(form.attr('action'),'POST',{challenge:token});
    r=request(r.headers.Location);grant=body(r).grant_id;
  }
  let page=cfg.mode==='pagination'?'/v2/catalog/page':'/v2/catalog', product;
  for(let n=0;n<3 && page;n++){
    const b=body(request(page));
    product=b.data.products.find(p=>p.variants.some(v=>v.in_stock));
    if(product) break;page=b.next;
  }
  if(!product){check(false,{'eligible product':x=>x});return;}
  const variant=product.variants.find(v=>v.in_stock);
  let r=request('/v2/carts','POST',{product_id:product.id,variant_id:variant.id,grant_id:grant});
  cart=body(r).id;let etag=r.headers.Etag;
  check(r,{'cart':r=>r.status===201});
  if(cfg.mode==='etag'||cfg.mode==='data'){
    const quantity=cfg.mode==='data'?rows[(__VU-1)%rows.length].quantity:(cfg.quantity || 2);
    r=request('/v2/carts/'+cart,'PATCH',{quantity:quantity},{'If-Match':etag});
    if(r.status===412){etag=request('/v2/carts/'+cart).headers.Etag;
      r=request('/v2/carts/'+cart,'PATCH',{quantity:quantity},{'If-Match':etag});}
    etag=r.headers.Etag;check(r,{'update':r=>r.status===200});
  }
  let reservation;
  if(cfg.mode==='reservation'){
    r=request('/v2/reservations','POST',{cart_id:cart});const url=body(r).status_url;
    for(let n=0;n<5;n++){
      sleep(.05);r=request(url);const b=body(r);
      if(b.status==='ready'){reservation=b.reservation_id;break;}
      if(b.status==='failed'){check(false,{'reservation ready':x=>x});return;}
    }
    if(!reservation) return;
  }
  r=request('/v2/orders','POST',{cart_id:cart,reservation_id:reservation},
     {'Idempotency-Key':cart,'If-Match':etag});
  check(r,{'accepted':r=>r.status===201 && body(r).accepted===true});
  if(cfg.mode==='threshold') raw('/v2/health');
}
export default function(){
  if(cfg.workload) {if(cfg.operation==='journey') flow();else work();}
  else if(cfg.mode==='errors'){login();for(let n=0;n<4;n++) work();}
  else flow();
}
export function browse(){work('/v2/browse');}
export function checkout(){flow();}
"""


def k6_reference(config):
    config = dict(config)
    rows = [{"username": f"bench-{i}", "password": "bench", "quantity": i + 1}
            for i in range(1, 17)]
    data = "JSON.parse(open('/fixtures/accounts.json'))" if config.get("mode") in ("data", "partition") else json.dumps(rows)
    if config.get("workload"):
        rates = config.get("rates", [config.get("rate", 5)])
        spans = [config.get("duration", 35)] if len(rates) == 1 else [15, 10, 10]
        start, scenarios = 0, {}
        vus = config.get("max_vus", 16 if len(rates) > 1 else 32)
        for index, (rate, span) in enumerate(zip(rates, spans)):
            scenarios[f"phase{index}"] = {"executor": "constant-arrival-rate", "rate": rate,
                "timeUnit": "1s", "duration": f"{span}s", "startTime": f"{start}s",
                "preAllocatedVUs": vus, "maxVUs": vus, "gracefulStop": "5s"}
            start += span
        options = {"scenarios": scenarios, "thresholds": {"checks": ["rate>=0.99"]}}
        options["noCookiesReset"] = True
        if config.get("pools"):
            options["scenarios"] = {name: {"executor": "constant-arrival-rate", "rate": pool["rate"],
                "timeUnit": "1s", "duration": f"{config.get('duration', 35)}s", "exec": name,
                "preAllocatedVUs": 32, "maxVUs": 32, "gracefulStop": "5s"}
                for name, pool in config["pools"].items()}
    else:
        options = {"scenarios": {"journeys": {"executor": "per-vu-iterations",
            "vus": config.get("users", 2), "iterations": config.get("iterations", 2),
            "maxDuration": "30s"}}, "thresholds": {"checks": ["rate==1"]}}
    if config.get("mode") == "threshold":
        options["thresholds"]["http_req_duration{name:checkout}"] = ["p(95)<250"]
    source = K6.replace("CONFIG", json.dumps(config)).replace("DATA", data).replace("OPTIONS", json.dumps(options))
    if config.get("mode") == "threshold":
        source = source.replace("redirects:0, timeout:'3s'", "tags:{name:path==='/v2/orders'?'checkout':path}, redirects:0, timeout:'3s'")
    return source


LOCUST = r"""
import json
import math
import re
import time
from gevent import sleep
from gevent.event import Event
from gevent.lock import Semaphore
from locust import HttpUser, LoadTestShape, task
from locust.exception import StopUser

CFG = __CONFIG__
MODE = CFG.get('mode', 'flow')
ROWS = __ROWS__
DURATION = CFG.get('duration', 35)
POOLS = CFG.get('pools') or [{'name': 'work', 'path': CFG.get('path', '/v2/work'),
                              'rate': CFG.get('rate', 5),
                              'operation': CFG.get('operation', 'work')}]
if isinstance(POOLS, dict):
    POOLS = [dict(pool, name=name) for name, pool in POOLS.items()]

class ArrivalPlan:
    def __init__(self):
        self.started = None
        self.counts = {pool['name']: 0 for pool in POOLS}
        self.lock = Semaphore()

    def slot(self, pool, number):
        rates = CFG.get('rates', [pool['rate']]) if not CFG.get('pools') else [pool['rate']]
        spans = [15, 10, 10] if len(rates) > 1 else [DURATION]
        offset = 0
        for rate, span in zip(rates, spans):
            count = int(rate * span)
            if number < count:
                return offset + number / rate + (
                    number * 0.002 if pool.get('operation') == 'journey' else 0)
            number -= count
            offset += span
        return None

    def next(self):
        with self.lock:
            if self.started is None:
                self.started = time.monotonic()
            scheduled = [(self.slot(pool, self.counts[pool['name']]), pool)
                         for pool in POOLS]
            scheduled = [(slot, pool) for slot, pool in scheduled if slot is not None]
            if not scheduled:
                return None
            slot, pool = min(scheduled, key=lambda item: item[0])
            self.counts[pool['name']] += 1
            deadline = self.started + slot
        sleep(max(0, deadline - time.monotonic()))
        return pool

ARRIVALS = ArrivalPlan()
OWNER = {'logins': 0, 'ready': Event(), 'lock': Semaphore()}

class MatrixUser(HttpUser):
    wait_time = lambda self: 0

    def on_start(self):
        number = int(self._p95_worker_id.rsplit('-', 1)[1])
        self.row = ROWS[(number - 1) % len(ROWS)]
        self.username = ('bench' if MODE == 'ownership' else
                         self.row['username'] if MODE in ('data', 'partition') else
                         'bench-' + str((number - 1) % 16 + 1))
        self.password = self.row.get('password', 'bench')
        self.csrf = ''
        self.cart = ''
        self.etag = ''
        self.grant = ''
        self.iteration = 0

    def raw(self, path, method='GET', data=None, headers=None, name=None):
        request_headers = {'Content-Type': 'application/json'}
        if self.csrf:
            request_headers['X-CSRF-Token'] = self.csrf
        request_headers.update(headers or {})
        with self.client.request(method, path, json=data, headers=request_headers,
                                 name=name, catch_response=True, allow_redirects=False,
                                 timeout=3) as response:
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get('error')
            allowed = (response.status_code < 400
                       or response.status_code == 401 and error in ('expired', 'session')
                       or response.status_code == 412 and error in ('etag', 'etag_conflict')
                       or response.status_code == 429 and path.split('?', 1)[0] == '/v2/work')
            if (path.startswith('/v2/jobs/') and response.status_code < 400
                    and body.get('status') == 'failed'
                    or path.split('?', 1)[0] == '/v2/orders' and method == 'POST'
                    and response.status_code < 400
                    and body.get('accepted') is not True):
                allowed = False
            if name == 'checkout' and response.elapsed.total_seconds() * 1000 >= 250:
                allowed = False
            if allowed:
                response.success()
            else:
                response.failure('request assertion failed')
                self.environment.process_exit_code = 99
            if body.get('csrf'):
                self.csrf = body['csrf']
            return response, body

    def login(self):
        self.csrf = ''
        form, _ = self.raw('/v2/login-form')
        token = re.search(
            r'<input(?=[^>]*\bname=["\']csrf["\'])(?=[^>]*\bvalue=["\']([^"\']+)["\'])[^>]*>',
            form.text, re.I)
        if not token:
            self.environment.process_exit_code = 99
            return
        response, body = self.raw('/v2/login', 'POST',
                                  {'username': self.username, 'password': self.password,
                                   'csrf': token.group(1)})
        if response.status_code == 302:
            location = response.headers.get('Location')
            if location:
                _, body = self.request(location)
        self.grant = body.get('grant_id', '')

    def recover(self):
        renewed, _ = self.raw('/v2/session/renew', 'POST', {})
        if renewed.status_code != 200:
            self.login()
        if self.cart:
            response, _ = self.raw('/v2/carts/' + self.cart + '/recover', 'POST', {})
            self.etag = response.headers.get('ETag', self.etag)

    def request(self, path, method='GET', data=None, headers=None, name=None):
        for _ in range(2):
            response, body = self.raw(path, method, data, headers, name)
            if response.status_code == 401 and body.get('error') in ('expired', 'session'):
                self.recover()
                continue
            return response, body
        return response, body

    def work(self, path='/v2/work'):
        if not self.csrf:
            self.login()
        for _ in range(5):
            response, body = self.request(path, 'POST', {})
            if response.status_code != 429:
                return
            sleep(float(response.headers.get('Retry-After', 0.01)) + 0.01)

    def flow(self):
        self.cart = ''
        self.etag = ''
        self.grant = ''
        self.login()
        if MODE == 'ownership':
            with OWNER['lock']:
                OWNER['logins'] += 1
                if OWNER['logins'] >= CFG.get('users', 2):
                    OWNER['ready'].set()
            if not OWNER['ready'].wait(5):
                self.environment.process_exit_code = 99
                return
            if self._p95_worker_id != 'user-1':
                sleep(0.5)
        if MODE == 'challenge':
            page, _ = self.request('/v2/challenge-form')
            action = re.search(r'<form\b[^>]*\baction=["\']([^"\']+)["\']', page.text, re.I)
            challenge = re.search(
                r'<input(?=[^>]*\bname=["\']challenge["\'])(?=[^>]*\bvalue=["\']([^"\']+)["\'])[^>]*>',
                page.text, re.I)
            if not action or not challenge:
                self.environment.process_exit_code = 99
                return
            response, _ = self.request(action.group(1), 'POST',
                                       {'challenge': challenge.group(1)})
            location = response.headers.get('Location')
            if location:
                _, body = self.request(location)
                self.grant = body.get('grant_id', '')
        page = '/v2/catalog/page' if MODE == 'pagination' else '/v2/catalog'
        product = None
        for _ in range(3):
            _, body = self.request(page)
            products = body.get('data', {}).get('products', [])
            product = next((item for item in products
                            if any(variant.get('in_stock') for variant in item.get('variants', []))),
                           None)
            if product:
                break
            page = body.get('next')
            if not page:
                break
        if not product:
            self.environment.process_exit_code = 99
            return
        variant = next(item for item in product['variants'] if item.get('in_stock'))
        response, body = self.request('/v2/carts', 'POST',
            {'product_id': product['id'], 'variant_id': variant['id'], 'grant_id': self.grant})
        self.cart = body.get('id', '')
        self.etag = response.headers.get('ETag', '')
        if not self.cart:
            return
        if MODE in ('etag', 'data'):
            quantity = self.row.get('quantity', 2) if MODE == 'data' else CFG.get('quantity', 2)
            headers = {'If-Match': self.etag}
            response, _ = self.request('/v2/carts/' + self.cart, 'PATCH',
                                       {'quantity': quantity}, headers)
            if response.status_code == 412:
                response, _ = self.request('/v2/carts/' + self.cart)
                self.etag = response.headers.get('ETag', self.etag)
                response, _ = self.request('/v2/carts/' + self.cart, 'PATCH',
                    {'quantity': quantity}, {'If-Match': self.etag})
            self.etag = response.headers.get('ETag', self.etag)
        reservation = None
        if MODE == 'reservation':
            _, body = self.request('/v2/reservations', 'POST', {'cart_id': self.cart})
            status_url = body.get('status_url')
            for _ in range(5):
                sleep(0.05)
                _, state = self.request(status_url)
                if state.get('status') == 'ready':
                    reservation = state.get('reservation_id')
                    break
                if state.get('status') == 'failed':
                    return
            if not reservation:
                return
        body = {'cart_id': self.cart}
        if reservation:
            body['reservation_id'] = reservation
        self.request('/v2/orders', 'POST', body,
                     {'Idempotency-Key': self.cart, 'If-Match': self.etag},
                     'checkout' if MODE == 'threshold' else None)

    @task
    def run_scenario(self):
        if CFG.get('workload'):
            pool = ARRIVALS.next()
            if pool is None:
                raise StopUser()
            if pool.get('operation') == 'journey':
                self.flow()
            else:
                self.work(pool['path'])
            return
        if MODE == 'errors':
            self.login()
            for _ in range(4):
                self.work()
        else:
            self.flow()
        self.iteration += 1
        if self.iteration >= CFG.get('iterations', 2):
            raise StopUser()

class MatrixShape(LoadTestShape):
    def __init__(self):
        super().__init__()
        self.first_tick = True

    def tick(self):
        users = (CFG.get('max_vus', 64 if CFG.get('capacity') or CFG.get('pools') else 32)
                 if CFG.get('workload') else CFG.get('users', 2))
        elapsed = self.get_run_time()
        if CFG.get('workload'):
            return None if elapsed >= DURATION + 5 else (users, max(1, users))
        if self.first_tick:
            self.first_tick = False
            return users, max(1, users)
        return None if self.runner.user_count == 0 else (users, max(1, users))
"""


def locust_reference(config):
    config = dict(config)
    rows = [{"username": f"bench-{i}", "password": "bench", "quantity": i + 1}
            for i in range(1, 17)]
    data = ("json.loads(open('/fixtures/accounts.json').read())"
            if config.get("mode") in ("data", "partition") else json.dumps(rows))
    return LOCUST.replace("__CONFIG__", repr(config)).replace("__ROWS__", data)


def props(element, values):
    for key, value in values.items():
        kind = "boolProp" if isinstance(value, bool) else "stringProp"
        ET.SubElement(element, kind, name=key).text = str(value).lower() if isinstance(value, bool) else str(value)


def node(parent, tag, values=None, **attributes):
    gui = {"TestPlan": "TestPlanGui", "ThreadGroup": "ThreadGroupGui", "LoopController": "LoopControlPanel",
           "OnceOnlyController": "OnceOnlyControllerGui", "IfController": "IfControllerPanel",
           "WhileController": "WhileControllerGui", "HTTPSamplerProxy": "HttpTestSampleGui",
           "CookieManager": "CookiePanel", "HeaderManager": "HeaderPanel", "Arguments": "ArgumentsPanel",
           "JSONPostProcessor": "JSONPostProcessorGui", "RegexExtractor": "RegexExtractorGui",
           "ResponseAssertion": "AssertionGui", "ConstantTimer": "ConstantTimerGui"}.get(tag, "TestBeanGUI")
    element = ET.SubElement(parent, tag, testname=attributes.pop("testname", f"{tag}-{next(NAMES.get())}"),
                            testclass=tag, guiclass=gui, enabled="true", **attributes)
    props(element, values or {})
    return element, ET.SubElement(parent, "hashTree")


def regex(parent, variable, expression, *, headers=False, template="$1$", default=None):
    node(parent, "RegexExtractor", {"RegexExtractor.useHeaders": str(headers).lower(),
        "RegexExtractor.refname": variable, "RegexExtractor.regex": expression,
        "RegexExtractor.template": template, "RegexExtractor.match_number": "1",
        "RegexExtractor.default": "${" + variable + "}" if default is None else default})


def extract(parent, variables):
    node(parent, "JSONPostProcessor", {"JSONPostProcessor.referenceNames": ";".join(variables),
        "JSONPostProcessor.jsonPathExprs": ";".join(variables.values()),
        "JSONPostProcessor.match_numbers": ";".join("1" for _ in variables),
        "JSONPostProcessor.defaultValues": ";".join("${" + key + "}" for key in variables)})


def condition(parent, variable):
    return node(parent, "IfController", {"IfController.condition": "${" + variable + "}",
        "IfController.useExpression": True, "IfController.evaluateAll": False})[1]


def sampler(parent, path, method="GET", body=None, *, extractors=None, assertion=None, recover=False):
    element, children = node(parent, "HTTPSamplerProxy", {"HTTPSampler.domain": "127.0.0.1",
        "HTTPSampler.port": "8000", "HTTPSampler.protocol": "http", "HTTPSampler.path": path,
        "HTTPSampler.method": method, "HTTPSampler.follow_redirects": False,
        "HTTPSampler.auto_redirects": False, "HTTPSampler.postBodyRaw": body is not None,
        "HTTPSampler.connect_timeout": "2000", "HTTPSampler.response_timeout": "3000"})
    args = ET.SubElement(element, "elementProp", name="HTTPsampler.Arguments", elementType="Arguments")
    collection = ET.SubElement(args, "collectionProp", name="Arguments.arguments")
    if body is not None:
        argument = ET.SubElement(collection, "elementProp", name="", elementType="HTTPArgument")
        props(argument, {"HTTPArgument.always_encode": False, "Argument.value": body,
                         "Argument.metadata": "=", "HTTPArgument.use_equals": False})
    manager, _ = node(children, "HeaderManager")
    headers = ET.SubElement(manager, "collectionProp", name="HeaderManager.headers")
    for key, value in {"Content-Type": "application/json", "X-CSRF-Token": "${csrf}",
                       "If-Match": "${etag}", "Idempotency-Key": "${cart}"}.items():
        header = ET.SubElement(headers, "elementProp", name="", elementType="Header")
        props(header, {"Header.name": key, "Header.value": value})
    extract(children, {"csrf": "$.csrf", **(extractors or {})})
    regex(children, "recover", r'"error"\s*:\s*"(?:expired|session)"', template="true", default="false")
    regex(children, "etag", r"(?im)^ETag:\s*([^\r\n]+)", headers=True)
    if assertion:
        item, _ = node(children, "ResponseAssertion", {"Assertion.test_field": "Assertion.response_data",
            "Assertion.test_type": "2", "Assertion.assume_success": False})
        tests = ET.SubElement(item, "collectionProp", name="Asserion.test_strings")
        ET.SubElement(tests, "stringProp", name="0").text = assertion
    if recover:
        branch = condition(parent, "recover")
        sampler(branch, "/v2/session/renew", "POST", "{}")
        login(condition(branch, "recover"))
        sampler(condition(branch, "hasCart"), "/v2/carts/${cart}/recover", "POST", "{}")
        sampler(branch, path, method, body, extractors=extractors, assertion=assertion)
    return children


def login(parent, username=None, reset=False):
    username = LOGIN_USER.get() if username is None else username
    children = sampler(parent, "/v2/login-form")
    regex(children, "form", r'''<input(?=[^>]*\bname=["']csrf["'])(?=[^>]*\bvalue=["']([^"']+)["'])[^>]*>''')
    if reset:
        regex(children, "cart", r"$^", default="MISSING")
        regex(children, "hasCart", r"$^", default="false")
    children = sampler(parent, "/v2/login", "POST",
        '{"username":"' + username + '","password":"bench","csrf":"${form}"}', assertion=r'"csrf"\s*:')
    regex(children, "location", r"(?im)^Location:\s*([^\r\n]+)", headers=True)
    regex(children, "redirect", r"(?im)^Location:", headers=True, template="true", default="false")
    sampler(condition(parent, "redirect"), "${location}", extractors={"grant": "$.grant_id"})


def group(parent, users, iterations, duration=None):
    element, children = node(parent, "ThreadGroup", {"ThreadGroup.num_threads": str(users),
        "ThreadGroup.ramp_time": "0", "ThreadGroup.scheduler": duration is not None,
        "ThreadGroup.duration": str(duration or ""), "ThreadGroup.on_sample_error": "continue"})
    controller = ET.SubElement(element, "elementProp", name="ThreadGroup.main_controller", elementType="LoopController")
    props(controller, {"LoopController.continue_forever": False, "LoopController.loops": str(-1 if duration else iterations)})
    node(children, "CookieManager", {"CookieManager.clearEachIteration": duration is None})
    return children


def pacing(parent, rate):
    element, _ = node(parent, "ConstantThroughputTimer", {"calcMode": "4"})
    ET.SubElement(element, "stringProp", name="throughput").text = str(rate * 60 * 1.01)


def jmeter_reference(config):
    NAMES.set(count())
    mode = config.get("mode", "flow")
    LOGIN_USER.set("bench" if config.get("capacity") else "${username}" if mode in ("data", "ownership") else "bench-${__threadNum}")
    if config.get("pools"):
        combined = None
        for name, pool in config["pools"].items():
            part = dict(config, **pool)
            part.pop("pools")
            tree = ET.fromstring(jmeter_reference(part))
            content = tree.find("hashTree/hashTree")
            content.find("ThreadGroup").set("testname", name)
            if combined is None:
                combined = tree
            else:
                combined.find("hashTree/hashTree").extend(list(content)[-2:])
        return ET.tostring(combined, encoding="unicode")
    root = ET.Element("jmeterTestPlan", version="1.2", properties="5.0", jmeter="5.6.3")
    _, plan = node(ET.SubElement(root, "hashTree"), "TestPlan")
    variables, _ = node(plan, "Arguments")
    arguments = ET.SubElement(variables, "collectionProp", name="Arguments.arguments")
    for key, value in {"username": "bench", "csrf": "MISSING", "cart": "MISSING",
            "etag": "MISSING", "hasCart": "false", "grant": "MISSING", "pending": "true", "ready": "false",
            "product": "MISSING", "variant": "MISSING", "quantity": "2"}.items():
        item = ET.SubElement(arguments, "elementProp", name=key, elementType="Argument")
        props(item, {"Argument.name": key, "Argument.value": value, "Argument.metadata": "="})
    workload = config.get("workload")
    users = config.get("users", 2)
    if workload:
        rate = config.get("rate", 5)
        users = min(64 if config.get("capacity") else 16, max(4, math.ceil(rate * .8) + 2))
    duration = config.get("duration", 38)
    children = group(plan, users, config.get("iterations", 2), duration if workload else None)
    if mode in ("data", "ownership"):
        node(children, "CSVDataSet", {"filename": "/fixtures/account-${__threadNum}.csv",
            "fileEncoding": "UTF-8", "variableNames": "", "delimiter": ",", "recycle": True,
            "stopThread": False, "shareMode": "shareMode.thread"})
    if workload and config.get("operation") != "journey":
        login(node(children, "OnceOnlyController")[1])
        work = sampler(children, config.get("path", "/v2/work"), "POST", "{}", assertion=r'"ok"\s*:\s*true', recover=True)
        pacing(work, config.get("rate", 5))
    else:
        login_start = len(children)
        login(children, reset=True)
        if workload:
            pacing(children[login_start + 1], config.get("rate", 1))
        if mode == "ownership":
            barrier = sampler(children, "/v2/health")
            node(barrier, "SyncTimer", {"groupSize": "2", "timeoutInMs": "5000"})
        selection = "$.data.products[?(@.variants[0].in_stock == true)]"
        catalog = sampler(children, "/v2/catalog", extractors={"product": selection + ".id", "variant": selection + ".variants[0].id"}, recover=mode == "ownership")
        if mode == "ownership":
            node(catalog, "ConstantTimer", {"ConstantTimer.delay": "${delay}"})
        created = sampler(children, "/v2/carts", "POST", '{"product_id":"${product}","variant_id":"${variant}","grant_id":"${grant}"}',
                          extractors={"cart": "$.id"}, assertion=r'"id"\s*:\s*"cart-')
        regex(created, "hasCart", r'"id"\s*:\s*"cart-', template="true", default="false")
        if mode == "data":
            sampler(children, "/v2/carts/${cart}", "PATCH", '{"quantity":${quantity}}', recover=True)
        if mode == "reservation":
            reserved = sampler(children, "/v2/reservations", "POST", '{"cart_id":"${cart}"}', extractors={"poll": "$.status_url"})
            regex(reserved, "pending", r'"status_url"', template="true", default="false")
            regex(reserved, "ready", r"$^", default="false")
            loop = node(children, "WhileController", {"WhileController.condition": "${pending}"})[1]
            polled = sampler(loop, "${poll}", extractors={"reservation": "$.reservation_id"}, assertion=r'"status"\s*:\s*"(?:pending|ready)"')
            node(polled, "ConstantTimer", {"ConstantTimer.delay": "50"})
            regex(polled, "pending", r'"status"\s*:\s*"pending"', template="true", default="false")
            regex(polled, "ready", r'"status"\s*:\s*"ready"', template="true", default="false")
            children = condition(children, "ready")
        sampler(children, "/v2/orders", "POST", '{"cart_id":"${cart}"' + (',"reservation_id":"${reservation}"' if mode == "reservation" else '') + '}',
                assertion=r'"accepted"\s*:\s*true', recover=mode in ("expiry", "ownership", "denial"))
    return ET.tostring(root, encoding="unicode")


def reference(tool, config):
    if tool == "k6":
        return k6_reference(config)
    if tool == "locust":
        return locust_reference(config)
    return jmeter_reference(config)
