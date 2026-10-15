"""Regression test for the negative-call_margin leak found in Playground S16.

Reproduces the exact live spot from live_round_v7_32.log:
  Turn, to_call 92, equity ~0.215, pot_odds ~0.261, aggressor with a
  bluffy/loose read (opp_bump -0.15). Pre-fix this CALLED - i.e. put 92 chips
  in with equity below the raw pot odds, a losing call on immediate odds.

Builds a real table dict in the documented /texas/pending-actions shape and
drives decide_live() end to end, with estimate_equity stubbed to the equity
actually observed so the assertion tests the threshold logic, not the RNG.
"""
import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import strategy as S
import live_agent as LA


def _table(to_call, pot, street, board):
    """Table where seat 1 (us) faces a bet from seat 4 on `street`."""
    return {
        "tableId": "test-table",
        "street": street,
        "potChips": pot,
        "currentBet": to_call,
        "bigBlindChips": 10,
        "smallBlindChips": 5,
        "selfSeatNumber": 1,
        "boardCards": board,
        "seats": [
            {"seatNumber": 1, "agentId": "me", "status": "Active",
             "stackChips": 900, "currentBetChips": 0, "totalCommittedChips": 100,
             "holeCards": ["7h", "2d"]},
            {"seatNumber": 4, "agentId": "villain", "status": "Active",
             "stackChips": 800, "currentBetChips": to_call,
             "totalCommittedChips": to_call, "holeCards": None},
        ],
        "recentEvents": [
            {"id": "e1", "type": "ActionTaken", "street": street,
             "summary": {"action": "bet", "seatNumber": 4, "amount": to_call}},
        ],
        "allowedActions": {
            "canFold": True, "canCall": True, "canRaise": True, "canCheck": False,
            "callChips": to_call, "callToAmount": to_call,
            "minRaiseTo": to_call * 2, "maxCommit": 900,
            "reasoningRequired": False,
        },
    }


def run_case(name, equity, to_call, pot, street, board, bluffy_villain):
    orig_equity = S.estimate_equity
    orig_stats = LA.get_opponent_stats
    S.estimate_equity = lambda *a, **k: equity
    # A loose+bluffy read is what produces opp_bump = -0.15.
    LA.get_opponent_stats = lambda aid: (
        {"playingStyle": {"tightness": "loose", "aggression": "aggressive"},
         "bluffPct": 0.75} if bluffy_villain else None)
    try:
        d = LA.decide_live(_table(to_call, pot, street, board))
    finally:
        S.estimate_equity = orig_equity
        LA.get_opponent_stats = orig_stats
    dbg = d.get("_debug", {})
    pot_odds = dbg.get("pot_odds")
    print(f"{name}: action={d['action']} amount={d['amount']} "
          f"equity={equity} pot_odds={pot_odds} opp_bump={dbg.get('opp_bump')}")
    return d, dbg


failures = []

# The exact live spot that leaked.
d, dbg = run_case("S16 turn (eq 0.215 < po 0.261, bluffy villain)",
                  0.215, 92, 260, "Turn", ["As", "Kd", "9c", "3h"], True)
if d["action"] == "call":
    failures.append("REGRESSION: called with equity below pot odds")
if dbg["pot_odds"] <= 0.215:
    failures.append("test setup wrong: pot_odds not above equity")

# The preflop twin from the same run.
d2, dbg2 = run_case("S16 preflop (eq 0.198 < po 0.278, bluffy villain)",
                    0.198, 5, 13, "Preflop", [], True)
if d2["action"] == "call":
    failures.append("REGRESSION: preflop called with equity below pot odds")

# Control: a bluffy read should STILL widen us vs a marginal-but-+EV call.
# equity comfortably above pot odds -> calling is correct and must survive.
d3, dbg3 = run_case("control (eq 0.45 > po 0.261, bluffy villain)",
                    0.45, 92, 260, "Turn", ["As", "Kd", "9c", "3h"], True)
if d3["action"] not in ("call", "raise"):
    failures.append(f"over-tightened: folded a clearly +EV call ({d3['action']})")

print()
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PASS: call_margin floored at 0; no calls below pot odds; +EV calls intact")
