"""Trusted bounded collector, invoked only after all generated processes stop."""

import base64
import json
import os
import stat
import sys


def collect(exports):
    files, errors = {}, {}
    for name, limit in exports.items():
        try:
            descriptor = os.open("/output/" + name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    errors[name] = "not a regular file"
                    continue
                if info.st_size > limit:
                    errors[name] = "artifact exceeds limit"
                    continue
                data = source.read(limit + 1)
                if len(data) > limit:
                    errors[name] = "artifact exceeds limit"
                    continue
                files[name] = base64.b64encode(data).decode("ascii")
        except OSError:
            errors[name] = "artifact missing or inaccessible"
    return {"files": files, "errors": errors}


if __name__ == "__main__":
    if sys.argv[1] == "status":
        try:
            with open("/control/state.json") as source:
                value = json.load(source)
        except FileNotFoundError:
            value = {"state": "running"}
    else:
        value = collect(json.loads(sys.argv[2]))
    print(json.dumps(value))
