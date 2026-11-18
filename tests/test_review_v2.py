import pytest

from p95bench.checkpoint_graders import flow_fractions, workload_fractions
from p95bench.execution import Trial


def flow_trial(counts=(2, 2), poll_offsets=None, actors=None):
    evidence, samples = [], []

    def request(identity, path, timestamp, worker, session=None, method="POST"):
        evidence.extend([{"kind": "start", "request": identity, "path": path,
                          "method": method, "time": timestamp},
                         {"kind": "end", "request": identity, "outcome": 200,
                          "time": timestamp + .001}])
        if session:
            evidence.append({"kind": "authenticated", "request": identity, "session": session})
        samples.append({"request": identity, "worker": str(worker), "assertion_failures": 0,
                        "status": "200", "success": True})

    for worker, count in enumerate(counts, 1):
        actor = actors[worker - 1] if actors else f"bench-{worker}"
        for iteration in range(count):
            key, timestamp = f"{worker}-{iteration}", iteration * 10 + worker * .01
            session, cart = "session-" + key, "cart-" + key
            request("login-" + key, "/v2/login", timestamp, worker)
            evidence.append({"kind": "login", "request": "login-" + key,
                             "session": session, "actor": actor, "time": timestamp})
            request(cart, "/v2/carts", timestamp + .1, worker, session)
            evidence.extend([{"kind": "cart", "request": cart, "resource": cart,
                              "session": session, "actor": actor},
                             {"kind": "mutation_authorized", "request": cart, "session": session}])
            reservation = "reservation-" + key
            if poll_offsets is not None:
                job = "job-" + key
                request(job, "/v2/reservations", timestamp + .15, worker, session)
                evidence.append({"kind": "reservation", "request": job,
                                 "resource": cart, "job": job})
                for index, offset in enumerate(poll_offsets):
                    identity = f"poll-{key}-{index}"
                    request(identity, "/v2/jobs/" + job, timestamp + offset, worker, session, "GET")
                    evidence.append({"kind": "job_state", "request": identity, "resource": job,
                                     "phase": "ready", "reservation": reservation})
            request("order-" + key, "/v2/orders", timestamp + 3, worker, session)
            evidence.append({"kind": "commit", "request": "order-" + key,
                             "resource": cart, "session": session, "actor": actor,
                             "time": timestamp + 3, "reservation": reservation})
    return Trial(True, "completed", 0, evidence, samples, [])


def reservation_control():
    return Trial(True, "completed", 0, [], [{"assertion_failures": 1}], [])


@pytest.mark.parametrize("offsets,expected", [
    ([.2, .3], 1), ([.2 + i * .06 for i in range(5)], 1),
    ([.2 + i * .06 for i in range(6)], 0), ([.2, .21], 0), ([], 0),
])
def test_reservation_bounds_are_enforced_per_job(offsets, expected):
    result = flow_fractions(flow_trial(poll_offsets=offsets), [reservation_control()],
                            {"mode": "reservation"})
    assert result["safety"] == expected


def test_reservation_polls_must_reference_their_reserved_job():
    trial = flow_trial(poll_offsets=[.2, .3])
    for event in trial.events:
        if event["kind"] == "start" and event["path"].startswith("/v2/jobs/"):
            event["path"] = "/v2/jobs/unreserved"
    assert flow_fractions(trial, [reservation_control()], {"mode": "reservation"})["safety"] == 0


def workload_trial(extra_starts=()):
    evidence = [{"kind": "login", "request": "login", "session": "session"}]
    samples = [{"request": "login", "worker": "1"}]
    for index, timestamp in enumerate([*range(36), *extra_starts]):
        evidence.extend([{"kind": "start", "request": index, "path": "/v2/work",
                          "method": "POST", "time": timestamp},
                         {"kind": "authenticated", "request": index, "session": "session"},
                         {"kind": "work", "request": index, "fault": None},
                         {"kind": "end", "request": index, "outcome": 200, "time": timestamp + .001}])
        samples.append({"request": index, "worker": "1"})
    evidence.sort(key=lambda event: event.get("time", -1))
    return Trial(True, "completed", 0, evidence, samples, [])


@pytest.mark.parametrize("extra,expected", [
    ([], 1), ([5.2], 1), ([5.2, 5.3], 0), ([34.8, 34.9], 0),
    ([4.8, 4.9], 1), ([35.2, 35.3], 1), ([5.9, 6.1], 1),
])
def test_completion_raw_ceiling_applies_to_each_scored_second(extra, expected):
    result = workload_fractions(workload_trial(extra), {"completion": True, "rate": 1, "raw_ceiling": 2})
    assert result["safety"] == expected
