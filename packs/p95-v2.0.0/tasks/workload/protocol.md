# Public shop protocol
Return one complete k6 1.0.0 JS or JMeter 5.6.3 JMX artifact, no explanation.
Target only http://127.0.0.1:8000 (k6 __ENV.BASE_URL; JMeter host/port properties or literals).
Accounts bench-1..bench-16 and bench all use password bench. Preserve per-user cookies.
GET /v2/login-form sets login_nonce and returns input[name=csrf], mixed with decoys/attribute variants.
POST /v2/login JSON {username,password,csrf} sets session cookie and returns {csrf,user}.
Use X-CSRF-Token on all authenticated non-GET calls. Successful mutations may return fresh csrf;
retain the previous token if absent. Every journey starts a fresh login/cart. Never guess IDs/cursors.
GET /v2/catalog returns {data:{products:[{id,variants:[{id,in_stock}]}]},next:null}.
GET /v2/catalog/page returns one product plus next (relative URL/null); traverse at most3 pages.
POST /v2/carts {product_id,variant_id,grant_id?} ->201 {id,csrf} plus ETag.
GET /v2/carts/{id} ->ETag. PATCH {quantity:integer 1..20} with If-Match. On412 re-read and retry
the same payload once. POST /v2/orders {cart_id,reservation_id?}, Idempotency-Key unique per cart
and stable on retry, If-Match when required ->201 {accepted:true,id,csrf}. HTTP200 accepted:false
is a business failure, not success. Negative controls must fail the intended check/assertion.
401 error=expired: POST /v2/session/renew {} with current CSRF, capture fresh csrf,
POST /v2/carts/{id}/recover {} if a cart exists, retry only the interrupted operation once.
401 error=session means revoked: renew may fail; get a fresh form/login, recover the cart.
401 error=denied is not expiry: fail its assertion. Bound recovery per operation, never restart checkout.
Login may return302 Location. Disable auto redirects; GET the dynamic Location exactly once.
GET /v2/challenge-form contains form action /v2/challenges/{id} and input[name=challenge].
POST that action {challenge}, current CSRF ->302 Location; GET Location ->{grant_id,csrf}.
Pass grant_id once to cart creation. Challenges/claims expire after2s and belong to the session.
POST /v2/reservations {cart_id} ->202 {status_url,csrf}; poll at least50ms apart, at most5 times.
Only status=ready supplies reservation_id; pending/failed must never reach checkout.
POST /v2/work or /v2/browse {} ->200 {ok:true}; scheduled429 needs Retry-After1s, at most4 retries.
500 is unexpected. /v2/health is not business work. For custom business_failure Rate, add one point
per raw /v2/work response; numerator=unexpected failures, excluding expected expiry/scheduled429;
denominator=all business responses including expected expiry. Keep checks truthful.
Native results and server audit are authoritative; stdout is not evidence. k6 handleSummary is disabled.
JMX declarative components only: TestPlan, ThreadGroup, Loop/OnceOnly/If/While/Generic/Transaction
controllers, HTTPSamplerProxy, Cookie/Header managers, JSONPostProcessor, RegexExtractor,
Response/Duration assertions, Constant/ConstantThroughput/Sync timers, CSVDataSet, Arguments.
No scripting/process components, listeners, custom classpaths/implementations or DTD/entities.
IfController uses boolean-expression mode. Allowed functions: __P, __property, __threadNum,
__counter, __time, __Random, __RandomString. No eval/Groovy/BeanShell/JavaScript/JEXL.
Fixtures are read-only /fixtures/<logical alias>. CPU1, RAM768MiB, PIDs256, FDs512, no egress.
