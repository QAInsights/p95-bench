"""Run a model-owned Locust file and export harness-owned request evidence."""

import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit


def run(model_path):
    import gevent
    from locust import HttpUser, LoadTestShape, events
    from locust.env import Environment

    spec = importlib.util.spec_from_file_location("p95_model", model_path)
    if spec is None or spec.loader is None:
        raise ValueError("Unable to load Locust model")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    users = [value for value in vars(module).values()
             if inspect.isclass(value) and value.__module__ == module.__name__
             and issubclass(value, HttpUser) and value is not HttpUser
             and not getattr(value, "abstract", False)]
    shapes = [value for value in vars(module).values()
              if inspect.isclass(value) and value.__module__ == module.__name__
              and issubclass(value, LoadTestShape) and value is not LoadTestShape]
    if not users or len(shapes) != 1:
        raise ValueError("Locust model must define HTTP User classes and one LoadTestShape")

    environment = Environment(user_classes=users, shape_class=shapes[0](),
                              host=os.environ["BASE_URL"], events=events)
    runner = environment.create_local_runner()
    sequence = iter(range(1, 1000000))
    for user_class in users:
        original_init = user_class.__init__

        def initialize(user, *args, _original=original_init, **kwargs):
            _original(user, *args, **kwargs)
            worker = f"user-{next(sequence)}"
            user._p95_worker_id = worker
            request = user.client.request

            def instrumented(method, url, *request_args, **request_kwargs):
                context = dict(request_kwargs.get("context") or {})
                context["worker"] = worker
                request_kwargs["context"] = context
                return request(method, url, *request_args, **request_kwargs)

            user.client.request = instrumented

        user_class.__init__ = initialize

    evidence_path = Path("/output/requests.jsonl")
    with evidence_path.open("w") as output:
        def record_request(request_type, name, response_time, response, exception, url, context, **_):
            if response is None:
                return
            request_id = response.headers.get("X-P95-Request-ID")
            worker = context.get("worker") if isinstance(context, dict) else None
            if not request_id or not worker:
                return
            record = {
                "request": request_id,
                "worker": worker,
                "name": name,
                "method": str(request_type),
                "path": urlsplit(url).path,
                "success": exception is None,
                "assertion_failures": int(exception is not None),
                "response_time": response_time,
                "active_users": runner.user_count,
            }
            output.write(json.dumps(record, separators=(",", ":")) + "\n")
            output.flush()

        def record_user_error(**_):
            if environment.process_exit_code is None:
                environment.process_exit_code = 1

        environment.events.request.add_listener(record_request)
        environment.events.user_error.add_listener(record_user_error)
        runner.start_shape()
        try:
            while runner.shape_greenlet is not None:
                gevent.sleep(0.05)
        finally:
            runner.quit()
    return environment.process_exit_code or 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    raise SystemExit(run(sys.argv[1]))
