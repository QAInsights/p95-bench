
import re
import time

from gevent import sleep
from gevent.event import Event
from gevent.lock import Semaphore
from locust import HttpUser, LoadTestShape, task
from locust.exception import StopUser

CFG = {'users': 2, 'iterations': 2, 'mode': 'decline'}
MODE = CFG.get('mode', 'flow')
ROWS = [{"username": "bench-1", "password": "bench", "quantity": 2}, {"username": "bench-2", "password": "bench", "quantity": 3}, {"username": "bench-3", "password": "bench", "quantity": 4}, {"username": "bench-4", "password": "bench", "quantity": 5}, {"username": "bench-5", "password": "bench", "quantity": 6}, {"username": "bench-6", "password": "bench", "quantity": 7}, {"username": "bench-7", "password": "bench", "quantity": 8}, {"username": "bench-8", "password": "bench", "quantity": 9}, {"username": "bench-9", "password": "bench", "quantity": 10}, {"username": "bench-10", "password": "bench", "quantity": 11}, {"username": "bench-11", "password": "bench", "quantity": 12}, {"username": "bench-12", "password": "bench", "quantity": 13}, {"username": "bench-13", "password": "bench", "quantity": 14}, {"username": "bench-14", "password": "bench", "quantity": 15}, {"username": "bench-15", "password": "bench", "quantity": 16}, {"username": "bench-16", "password": "bench", "quantity": 17}]
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
):
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
            form.text, re.IGNORECASE)
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
            response, _ = self.request(path, 'POST', {})
            if response.status_code != 429:
                return
            sleep(float(response.headers.get('Retry-After', 0.01)) + 0.01)

    def flow(self):
        self.client.cookies.clear()
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
            action = re.search(
                r'<form\b[^>]*\baction=["\']([^"\']+)["\']', page.text, re.IGNORECASE)
            challenge = re.search(
                r'<input(?=[^>]*\bname=["\']challenge["\'])(?=[^>]*\bvalue=["\']([^"\']+)["\'])[^>]*>',
                page.text, re.IGNORECASE)
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
