"""Verify `decided_by` attribution populates correctly across branch types.

Exists because v21's log could not distinguish "the stalled guard folded this"
from "pot odds folded it anyway" - that required reconstructing the decision by
hand from equity vs pot_odds arithmetic. This asserts the log answers it
directly.

Every message in the codebase must map to a label. An `unmapped:` prefix means
a branch was added without updating _DECIDED_BY, and that is a test failure -
the whole point is that attribution is never silently missing.
"""
import re
import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as LA
import strategy as S

failures = []


def table(street, board, hole, pot, to_call, stack=400, committed=0,
          can_check=False, can_raise=True, events=None):
    return {
        "tableId": "t", "street": street, "potChips": pot,
        "currentBet": to_call, "bigBlindChips": 2, "smallBlindChips": 1,
        "selfSeatNumber": 1, "boardCards": board,
        "seats": [
            {"seatNumber": 1, "agentId": "me", "status": "Active",
             "stackChips": stack, "currentBetChips": 0,
             "totalCommittedChips": committed, "holeCards": hole},
            {"seatNumber": 4, "agentId": "v", "status": "Active",
             "stackChips": 900, "currentBetChips": to_call,
             "totalCommittedChips": to_call, "holeCards": None},
        ],
        "recentEvents": events or [],
        "allowedActions": {
            "canFold": True, "canCall": to_call > 0, "canCheck": can_check,
            "canBet": can_check, "canRaise": can_raise,
            "callChips": to_call, "callToAmount": to_call,
            "minBet": 2, "minRaiseTo": max(4, to_call * 2), "maxCommit": stack,
            "reasoningRequired": False,
        },
    }


def run(t, equity):
    orig = S.estimate_equity
    S.estimate_equity = lambda *a, **k: equity
    try:
        d = LA.decide_live(t)
    finally:
        S.estimate_equity = orig
    return d, d.get("_debug", {})


BET = [{"id": "e1", "type": "ActionTaken", "street": "Turn",
        "summary": {"action": "bet", "seatNumber": 4}}]

cases = [
    # (label, table, equity, expected decided_by, expected action)
    ("stalled guard (turn, high card, low eq)",
     table("Turn", ["7h", "3s", "Ad", "6h"], ["Qs", "8s"], 49, 16, events=BET),
     0.25, "stalled_guard", "fold"),
    ("pot-odds fold (turn, PAIR so guard cannot apply)",
     table("Turn", ["7h", "3s", "Ad", "6h"], ["Ah", "8s"], 49, 40, events=BET),
     0.10, "pot_odds_fold", "fold"),
    ("pot-odds call (turn, pair, price is right)",
     table("Turn", ["7h", "3s", "Ad", "6h"], ["Ah", "8s"], 100, 10, events=BET),
     0.55, "pot_odds_call", "call"),
    ("value raise (turn, strong)",
     table("Turn", ["7h", "3s", "Ad", "6h"], ["Ah", "As"], 60, 10, events=BET),
     0.95, "value_raise", "raise"),
    ("free check (turn, nobody bet)",
     table("Turn", ["7h", "3s", "Ad", "6h"], ["Qs", "8s"], 40, 0, can_check=True),
     0.20, "check_free", "check"),
]

for label, t, eq, want_by, want_act in cases:
    d, dbg = run(t, eq)
    got_by, got_act = dbg.get("decided_by"), d.get("action")
    ok = "OK " if (got_by == want_by and got_act == want_act) else "FAIL"
    print(f"{ok} {label}\n     -> action={got_act} decided_by={got_by} "
          f"(want {want_act}/{want_by})")
    if got_act != want_act:
        failures.append(f"{label}: action {got_act}, wanted {want_act}")
    if got_by != want_by:
        failures.append(f"{label}: decided_by {got_by}, wanted {want_by}")

# --- the v21 ambiguity, resolved: same fold, two different causes ---
print("\nthe v21 ambiguity these labels exist to resolve:")
_, a = run(table("Turn", ["7h", "3s", "Ad", "6h"], ["Qs", "8s"], 49, 16, events=BET), 0.25)
_, b = run(table("Turn", ["7h", "3s", "Ad", "6h"], ["Ah", "8s"], 49, 40, events=BET), 0.10)
print(f"  high-card fold -> {a.get('decided_by')}")
print(f"  pair fold      -> {b.get('decided_by')}")
if a.get("decided_by") == b.get("decided_by"):
    failures.append("two folds with different causes still share a label")

# --- no message anywhere may be unmapped ---
src = open("C:/Users/billy/poker-arena/live_agent.py", encoding="utf-8").read()
msgs = set(re.findall(r'msg = "([^"]+)"', src)) | set(re.findall(r', "([^"]+)"\n', src))
literal = set(re.findall(r'action, amount, msg = [^"]*"[a-z-]+", [^,]+, "([^"]+)"', src))
unmapped = [m for m in literal if m not in LA._DECIDED_BY]
print(f"\nmessage coverage: {len(literal)} literal branch messages found, "
      f"{len(unmapped)} unmapped")
for m in unmapped:
    failures.append(f"branch message not in _DECIDED_BY: {m!r}")

print()
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PASS: decided_by attributes every tested branch, distinguishes the two "
      "fold causes that were ambiguous in v21, and no branch message is unmapped")
