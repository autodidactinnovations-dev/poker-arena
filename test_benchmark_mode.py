"""Benchmark-mode plumbing, verified WITHOUT starting a scored run.

Eval S1 is entered via POST /texas/benchmark/start and polled via
/texas/benchmark/status, which returns {match, table, participant} rather than
pending-actions' {tables, activeTables, lobby, participant}.

The populated response shape could not be learned from outside: no OpenAPI
spec, no skill file over the API, no public docs, and no attempts endpoint. So
benchmark_poll() is written to tolerate several plausible shapes, and this
pins that tolerance. What is CONFIRMED from probing (a 400 on an empty body, a
404 on an unknown competition id, neither of which starts anything):

    POST /texas/benchmark/start  requires exactly {competitionId}
    GET  /texas/benchmark/status returns keys {match, table, participant}

Everything below drives the normalizer directly with synthetic payloads. No
network, no attempt consumed.
"""
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
    print("  %-58s %s" % (label, "ok" if got == want else "FAIL got %r" % (got,)))


def table(acting=1, mine=1, actions=("fold", "call", "raise")):
    return {"id": "t1", "tableId": "t1", "street": "Preflop",
            "actingSeatNumber": acting, "selfSeatNumber": mine,
            "allowedActions": {"availableActions": list(actions)}}


print("_table_is_actionable - the guard that stops a re-POST spin while it is")
print("the reference panel's turn (pending-actions pre-filters, status does not):")
check("our turn, actions available", L._table_is_actionable(table()), True)
check("panel's turn (acting != self)", L._table_is_actionable(table(acting=2)), False)
check("no available actions", L._table_is_actionable(table(actions=())), False)
check("actingSeatNumber absent -> assume actionable",
      L._table_is_actionable({"allowedActions": {"availableActions": ["fold"]}}), True)
check("not a dict", L._table_is_actionable(None), False)
check("empty dict", L._table_is_actionable({}), False)

print()
print("benchmark_poll normalization. The populated shape is UNVERIFIED, so a")
print("wrong guess must degrade to 'nothing to do', never to a crash:")

_calls = []


def fake_call(shape):
    def _c(method, path, body=None, query=None):
        _calls.append((method, path))
        return 200, shape
    return _c


orig_call = L.call
try:
    # single table object, our turn
    L.call = fake_call({"match": {"id": "m1"}, "table": table(),
                        "participant": {"totalChips": 1000}})
    st, env = L.benchmark_poll()
    check("single table, our turn -> 1 actionable", len(env["tables"]), 1)
    check("  participant passed through", env["participant"], {"totalChips": 1000})
    check("  lobby present and empty", env["lobby"], {})
    check("  match preserved as _match", env["_match"], {"id": "m1"})

    # single table object, panel's turn
    L.call = fake_call({"match": {}, "table": table(acting=2), "participant": {}})
    st, env = L.benchmark_poll()
    check("panel's turn -> 0 actionable", len(env["tables"]), 0)
    check("  but still counted active", len(env["activeTables"]), 1)

    # a LIST of tables instead of one object
    L.call = fake_call({"match": {}, "table": [table(), table(acting=2)],
                        "participant": {}})
    st, env = L.benchmark_poll()
    check("list of 2, one actionable", len(env["tables"]), 1)
    check("  both counted active", len(env["activeTables"]), 2)

    # no table yet (match starting up)
    L.call = fake_call({"match": {"status": "starting"}, "table": None,
                        "participant": None})
    st, env = L.benchmark_poll()
    check("table None -> no tables", env["tables"], [])
    check("  participant None -> {}", env["participant"], {})

    # the exact idle payload the live endpoint returns today
    L.call = fake_call({"match": None, "table": None, "participant": None})
    st, env = L.benchmark_poll()
    check("real idle payload -> empty envelope", env["tables"], [])

    # hostile shapes: must not raise
    for bad in ({"table": "a string"}, {"table": [None, 7]}, {}):
        L.call = fake_call(bad)
        try:
            st, env = L.benchmark_poll()
            check("tolerates %r" % (bad,), isinstance(env.get("tables"), list), True)
        except Exception as e:
            failures.append(f"benchmark_poll raised on {bad!r}: {e}")
            print("  tolerates %-46r FAIL raised %s" % (bad, e))

    # non-200 passes straight through untouched
    L.call = lambda *a, **k: (503, {"error": "boom"})
    st, resp = L.benchmark_poll()
    check("non-200 passed through", (st, resp), (503, {"error": "boom"}))

    print()
    print("poll_pending dispatch - the whole point is that the decision path")
    print("downstream never learns which mode it is in:")
    L.call = fake_call({"tables": [], "activeTables": [], "lobby": {},
                        "participant": {}})
    L.BENCHMARK_MODE = False
    _calls.clear()
    L.poll_pending()
    check("lobby mode hits pending-actions",
          _calls[-1], ("GET", "/texas/pending-actions"))
    L.BENCHMARK_MODE = True
    _calls.clear()
    L.poll_pending()
    check("benchmark mode hits benchmark/status",
          _calls[-1], ("GET", "/texas/benchmark/status"))
finally:
    L.call = orig_call
    L.BENCHMARK_MODE = False

print()
print("payment guards (S4 is a PAID competition - per-run fee in USD-T0):")
check("402 on join is terminal", L.classify_join(402, {}), "terminal")
check("S4 listed as paid",
      "cmsy5imvg2h6zeczzzw1fc7ya" in L.PAID_COMPETITIONS, True)
check("S1 NOT listed as paid",
      "seed_poker_eval_s1" in L.PAID_COMPETITIONS, False)

print()
if failures:
    print("FAIL (%d):" % len(failures))
    for f in failures:
        print("  " + f)
    raise SystemExit(1)
print("PASS: benchmark plumbing normalizes to the pending-actions envelope,")
print("tolerates unverified shapes without raising, and dispatches correctly.")
print("No scored attempt was consumed to verify any of this.")
