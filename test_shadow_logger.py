"""19 shadow logger. Proves the RECORD LANDS and the ACTION IS UNCHANGED.

Written as an end-to-end test on purpose. Two bugs in this project were caught
only by driving decide_live rather than reading the code: the BB exemption keyed
off a variable that was None for exactly the hands it targeted, and this
logger's first draft referenced `my_total_committed`, which is computed ~65
lines BELOW the firing site and would have raised NameError on every fire.
Reading the patch would not have found either.
"""
import json
import os
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L

# PATH ORDER MATTERS BOTH WAYS.
# test_aggression_fold drives real decisions in its MODULE BODY and now sets
# SHADOW_PATH to its own file on import - so this test must redirect AFTER
# importing it, or that import silently steals the path and nothing lands in
# TMP. The earlier version of this file had the opposite bug: it redirected
# after the import was already firing into the PRODUCTION file, writing
# synthetic rows (hand_id "t1", aggression 18.0) into real data. Twice.
import glob

TMP = "C:/Users/billy/poker-arena/_test_shadow.jsonl"
DIR = "C:/Users/billy/poker-arena"

# SNAPSHOT EVERY PRODUCTION SHADOW LOG, not just §19's.
# Driving decide_live fires EVERY shadow hook, and new hooks keep arriving from
# other sessions (§20 flop no-pair, §22 ranged equity, §24 beat rate). Guarding
# only the one log this test is about is why §22 and §24 each ended up with
# synthetic rows. Checking all of them by size means a hook added tomorrow
# makes THIS TEST FAIL instead of quietly corrupting real data - detection that
# survives loggers nobody has written yet.
_prod_before = {p: os.path.getsize(p) for p in glob.glob(os.path.join(DIR, "shadow_*.jsonl"))}

import test_aggression_fold as T   # reuse its table builder

# Redirect every known logger AFTER the import, deliberately: that import
# drives real decisions in its module body and would otherwise steal the path.
L.SHADOW_PATH = TMP
for _attr, _name in (("S22_PATH", "_test_s22.jsonl"),
                     ("S24_PATH", "_test_s24.jsonl"),
                     ("FLOP_NOPAIR_PATH", "_test_flopnopair.jsonl")):
    if hasattr(L, _attr):
        setattr(L, _attr, os.path.join(DIR, _name))
if os.path.exists(TMP):
    os.remove(TMP)

# OPT IN DELIBERATELY, AFTER redirecting. Shadow writes default to off and only
# main() turns them on, so this test must enable them by hand - and must only do
# so once every path above points at a temp file. The order matters: enabling
# before redirecting is exactly how production logs got polluted before.
L.LIVE = True

failures = []


def check(label, got, want):
    ok = got == want
    if not ok:
        failures.append("%s: got %r want %r" % (label, got, want))
    print("  %-64s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))


def records():
    if not os.path.exists(TMP):
        return []
    return [json.loads(l) for l in open(TMP, encoding="utf-8") if l.strip()]


# ARENA-SHAPED TEST IDS. The shadow loggers now drop any hand_id that is not an
# arena cuid ('cm' + 20+ chars) - a class fix added after test runs put 350+
# synthetic rows into the production logs three separate times. That guard is
# correct, and it broke THIS test, whose whole purpose is asserting that rows
# land: its old ids ('t1', 't_short', ...) are now rejected before the write.
# So the ids here are cuid-shaped while staying obviously synthetic, and the
# TMP redirect plus assertion 8 still prove production is never touched.
def tid(n):
    return "cmtest%016d" % n


def with_id(t, n):
    t["id"] = t["tableId"] = tid(n)
    return t


print("1. A FIRING WRITES A FOLD RECORD, AND THE ACTION IS STILL FOLD")
t = with_id(T.table(T.WEAK[0], T.WEAK[1], to_call=30, n_aggro=3), 1)
res = L.decide_live(t)
d = res.get("_debug") or {}
check("action unchanged", (res["action"], d.get("decided_by")), ("fold", "aggression_fold"))
recs = records()
check("exactly one record written", len(recs), 1)
r = recs[0] if recs else {}
check("stage", r.get("stage"), "fold")
check("resolved starts false", r.get("resolved"), False)
check("hand_id == table id", r.get("hand_id"), t["id"])
for f in ("street", "equity", "pot", "to_call", "aggression_score",
          "made_category", "shadow_action", "hero_committed_at_fold",
          "board_at_fold", "hole_cards"):
    check("field present: %s" % f, f in r, True)
check("hole_cards captured", r.get("hole_cards"), list(T.WEAK[1]))
check("board_at_fold captured", r.get("board_at_fold"), list(T.WEAK[0]))
check("hero_committed_at_fold is a number", isinstance(r.get("hero_committed_at_fold"), (int, float)), True)

print("2. shadow_action IS COMPUTED, NOT ASSUMED")
# The whole point: where the price test would ALSO have folded, the record must
# say "fold", not imply we prevented a call that was never going to happen.
check("shadow_action is a real action", r.get("shadow_action") in ("call", "fold", "check"), True)
print("     (this spot's shadow_action = %r)" % r.get("shadow_action"))

print("3. SETTLEMENT JOINS ON hand_id")
L.shadow_record_settlement({
    "id": t["id"],
    "boardCards": ["2c", "7d", "9h", "Js", "Ad"],
    "winners": [{"seatNumber": 2, "agentId": "opp2", "amount": 260}],
    "seats": [{"seatNumber": 1, "payoutChips": 0},
              {"seatNumber": 2, "payoutChips": 260}],
    "chipDelta": -30,
})
recs = records()
check("settlement appended", len(recs), 2)
s = recs[-1] if len(recs) > 1 else {}
check("settled stage", s.get("stage"), "settled")
check("joins on same hand_id", s.get("hand_id"), t["id"])
check("resolved true", s.get("resolved"), True)
check("board_ran_out true on a 5-card board", s.get("board_ran_out"), True)
check("winners verbatim from arena", s.get("winners"),
      [{"seatNumber": 2, "agentId": "opp2", "amount": 260}])
check("payout_chips captured", s.get("payout_chips"), {"1": 0, "2": 260})

print("4. A SETTLEMENT WE NEVER FOLDED IN IS IGNORED")
before = len(records())
L.shadow_record_settlement({"id": tid(99), "boardCards": [],
                            "winners": [], "seats": []})
check("unrelated settlement writes nothing", len(records()), before)

print("5. A SHORT BOARD IS RECORDED AS *NOT* RUN OUT")
t2 = T.table(T.WEAK[0], T.WEAK[1], to_call=30, n_aggro=3)
t2 = with_id(t2, 2)
L.decide_live(t2)
L.shadow_record_settlement({"id": tid(2), "boardCards": ["2c", "7d", "9h"],
                            "winners": [], "seats": []})
s2 = records()[-1]
check("board_ran_out false on a 3-card board", s2.get("board_ran_out"), False)
check("still marked resolved", s2.get("resolved"), True)

print("6. THE LOGGER CANNOT BREAK PLAY")
_orig = L._shadow_append
L._shadow_append = lambda rec: (_ for _ in ()).throw(IOError("disk full"))
t3 = T.table(T.WEAK[0], T.WEAK[1], to_call=30, n_aggro=3)
t3 = with_id(t3, 3)
res3 = L.decide_live(t3)
check("decision survives a failing logger", res3["action"], "fold")
L._shadow_append = _orig

print("7. FLAG OFF -> NO FIRING, NO RECORD")
L.AGGRESSION_FOLD_ENABLED = False
before = len(records())
t4 = T.table(T.WEAK[0], T.WEAK[1], to_call=30, n_aggro=3)
t4 = with_id(t4, 4)
L.decide_live(t4)
check("no record when the rule is disabled", len(records()), before)
L.AGGRESSION_FOLD_ENABLED = True

os.remove(TMP)

print("8. NO PRODUCTION SHADOW LOG WAS TOUCHED")
# Checks every shadow_*.jsonl, including any that appeared since the snapshot.
_prod_after = {p: os.path.getsize(p) for p in glob.glob(os.path.join(DIR, "shadow_*.jsonl"))}
for _p in sorted(set(_prod_before) | set(_prod_after)):
    check("unchanged: %s" % os.path.basename(_p),
          _prod_after.get(_p), _prod_before.get(_p))
for _tmp in ("_test_s22.jsonl", "_test_s24.jsonl", "_test_flopnopair.jsonl"):
    _f = os.path.join(DIR, _tmp)
    if os.path.exists(_f):
        os.remove(_f)

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS: shadow records land with every specced field, join on hand_id at")
print("settlement, distinguish ran-out from merely-settled, take winners")
print("verbatim, and cannot affect the action taken.")
