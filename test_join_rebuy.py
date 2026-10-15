"""Join-path rebuy. Proves the OUTCOME: a run that STARTS on a sub-big-blind
stack rebuys and gets in, instead of retrying a join that cannot succeed.

Driven through main()'s real join loop with `call` faked, because that is where
the defect lived. classify_join already returned "rebuy" for this error - the
bug was that the loop never acted on it, so a test asserting on the classifier
would have passed while production burned two runs to zero decisions
(PRINCIPLES 13: test the outcome, not the reported trigger).
"""
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L

failures = []


def check(label, got, want):
    ok = got == want
    if not ok:
        failures.append("%s: got %r want %r" % (label, got, want))
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))


INSUFFICIENT = {"error": "insufficient_chips",
                "message": "total chips are below one big blind. "
                           "Call /texas/rebuy if rebuy is available."}


def run_case(label, rebuy_status, join_after_rebuy, expect_join_calls,
             expect_rebuys, expect_played):
    """Fake the arena. Returns nothing; asserts via check()."""
    # "reached the hand loop" is measured by whether pending-actions was ever
    # polled - that is the loop's first act. An earlier version of this test
    # used "main() returned without raising", which is ALSO true on the abort
    # path, so cases 2 and 3 passed while measuring nothing.
    calls = {"join": 0, "rebuy": 0, "pending": 0}
    state = {"rebought": False}

    def fake_call(method, path, body=None, query=None):
        if path == "/texas/join":
            calls["join"] += 1
            if state["rebought"] and join_after_rebuy:
                return 200, {"kind": "queued", "lobby": {"position": 1},
                             "participant": {"totalChips": 1001}}
            return 409, dict(INSUFFICIENT)
        if path == "/texas/rebuy":
            calls["rebuy"] += 1
            if rebuy_status == 200:
                state["rebought"] = True
                return 200, {"participant": {"rebuyCount": 6, "totalChips": 1001}}
            return rebuy_status, {"error": "nope"}
        if path == "/texas/pending-actions":
            calls["pending"] += 1
            raise SystemExit(0)      # seated; stop before playing hands
        return 200, {}

    orig_call, orig_log = L.call, L.log
    L.call = fake_call
    L.log = lambda *a, **k: None
    try:
        sys.argv = ["la", "0"]          # duration 0 -> exits the hand loop at once
        L.main()
    except SystemExit:
        pass
    except Exception as e:
        failures.append("%s raised %r" % (label, e))
    finally:
        L.call, L.log = orig_call, orig_log
    print("--- %s ---" % label)
    check("join attempts", calls["join"], expect_join_calls)
    check("rebuy calls", calls["rebuy"], expect_rebuys)
    check("reached the hand loop", calls["pending"] > 0, expect_played)


print("1. SUB-BIG-BLIND AT STARTUP -> REBUY, THEN JOIN SUCCEEDS")
# join refused once, rebuy succeeds, second join gets in.
run_case("insufficient_chips then rebuy", 200, True,
         expect_join_calls=2, expect_rebuys=1, expect_played=True)

print("2. ONE REBUY ONLY - NEVER LOOPS SPENDING REBUYS")
# Rebuy succeeds but join STILL refuses: must stop, not rebuy again.
run_case("join still refused after rebuy", 200, False,
         expect_join_calls=2, expect_rebuys=1, expect_played=False)

print("3. A PAID REBUY IS REFUSED, NOT PAID")
run_case("rebuy -> 402", 402, False,
         expect_join_calls=1, expect_rebuys=1, expect_played=False)

print("4. THE CLASSIFIER STILL LABELS THE ERROR CORRECTLY")
check("classify_join(409, insufficient_chips)",
      L.classify_join(409, INSUFFICIENT), "rebuy")
check("classify_join(200, ...) unchanged", L.classify_join(200, {}), "ok")
check("classify_join(402, ...) still terminal", L.classify_join(402, {}), "terminal")

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS: a run starting on a sub-big-blind stack now rebuys once and")
print("joins; it never loops on rebuys and never pays for one.")
