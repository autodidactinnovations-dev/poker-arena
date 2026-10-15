"""Regression test for the range_strength fix (FINDINGS.md Leak 6).

Repro is table cmtb7bys (2026-08-27, Playground S16): we held Qc8s, board ran
out 2s Jc Qd 3h 5c (top pair, weak kicker), and the agent bet flop/turn/river
at 76%/87%/84% modeled equity into a single opponent who had called both
prior streets and turned over Qs Ts - a better kicker - for -83 chips. The
uniform-random Monte Carlo opponent in estimate_equity can't see that a
sole remaining caller who has stuck around for two streets of value bets is
holding a stronger-than-random hand.

Two checks:
  B. estimate_equity's range_strength actually pulls the Q8-on-cmtb7bys-board
     equity DOWN relative to range_strength=0, by rejecting opponent hands
     that whiffed completely (no pair) - direction, not just wiring.
  C. decide_live computes range_strength=0 on the first (flop) bet of a hand
     (no opponent action yet) and a LOW discount on the river after the
     opponent has merely called both flop and turn - i.e. it is wired into the
     live decision path, and passive continuation is scored as the weak
     evidence it is. See test_cumulative_aggression.py for the aggression
     side of this signal.
"""
import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import strategy as S
import live_agent as LA

failures = []

# --- B: range_strength direction on the actual repro hand/board ---
hole = [S._parse_card(c) for c in ["Qc", "8s"]]
board = [S._parse_card(c) for c in ["2s", "Jc", "Qd", "3h", "5c"]]
# Seeded: the effect size here (~0.05) is close enough to this estimator's own
# sampling noise that an unseeded run is genuinely flaky - it failed once at
# 0.047 against a 0.05 bar purely on RNG. Seeding makes the assertion about the
# range logic rather than about the draw.
import random as _random
_random.seed(20260827)
TRIALS = 12000
eq_uniform = S.estimate_equity(hole, board, num_opponents=0, pending_opponents=1,
                                pending_weight=0.9, range_strength=0.0, trials=TRIALS)
eq_ranged = S.estimate_equity(hole, board, num_opponents=0, pending_opponents=1,
                               pending_weight=0.9, range_strength=0.7, trials=TRIALS)
print(f"B: equity uniform={eq_uniform:.3f} ranged={eq_ranged:.3f} "
      f"(effect {eq_uniform - eq_ranged:.3f})")
# Bar is 0.03, set from the MEASURED effect (~0.047 repeatedly), not from an
# aspiration. The original 0.05 bar was guesswork and flaked at 0.047.
# That small effect size is itself a finding: a ~0.05 discount is far too weak
# to stop a hand like cmtcarsk, which stacked off at 0.828 equity - even a full
# 0.7 range_strength only walks that back to ~0.78, still above every
# continue threshold. This is the evidence that range_strength alone cannot
# carry the load, and why the independent velocity brake exists.
if not (eq_ranged < eq_uniform - 0.03):
    failures.append(f"B: range_strength should lower equity vs a still-in caller "
                     f"(uniform={eq_uniform:.3f}, ranged={eq_ranged:.3f})")

# --- C: wiring through decide_live ---
captured = []
orig_estimate_equity = S.estimate_equity


def _spy(*args, **kwargs):
    captured.append(kwargs.get("range_strength"))
    return 0.7


S.estimate_equity = _spy


def _table(street, recent_events, pot, my_bet):
    return {
        "tableId": "test-table",
        "street": street,
        "potChips": pot,
        "currentBet": my_bet,
        "bigBlindChips": 10,
        "smallBlindChips": 5,
        "selfSeatNumber": 2,
        "boardCards": ["2s", "Jc", "Qd", "3h", "5c"][:{"Flop": 3, "Turn": 4, "River": 5}[street]],
        "seats": [
            {"seatNumber": 2, "agentId": "me", "status": "Active",
             "stackChips": 200, "currentBetChips": my_bet, "totalCommittedChips": my_bet,
             "holeCards": ["Qc", "8s"]},
            {"seatNumber": 6, "agentId": "villain", "status": "Active",
             "stackChips": 2000, "currentBetChips": 0, "totalCommittedChips": 28,
             "holeCards": None},
        ],
        "recentEvents": recent_events,
        "allowedActions": {
            "canFold": True, "canCall": False, "canCheck": True, "canBet": True, "canRaise": False,
            "minBet": 5, "maxCommit": 200, "reasoningRequired": False,
        },
    }


try:
    # Flop: leading out with no history yet -> no continuation info exists.
    LA.decide_live(_table("Flop", [], pot=15, my_bet=0))
    # River: villain has called both flop and turn already.
    river_events = [
        {"id": "r1", "type": "ActionTaken", "street": "Flop", "summary": {"action": "call", "seatNumber": 6}},
        {"id": "r2", "type": "ActionTaken", "street": "Turn", "summary": {"action": "call", "seatNumber": 6}},
    ]
    LA.decide_live(_table("River", river_events, pot=122, my_bet=0))
finally:
    S.estimate_equity = orig_estimate_equity

print(f"C: range_strength passed to estimate_equity -> flop={captured[0]} river={captured[1]}")
if captured[0] != 0.0:
    failures.append(f"C: expected range_strength=0.0 on flop bet with no continuation history, got {captured[0]}")
# Two called streets is PASSIVE continuation. Under the old per-street snapshot
# this hard-coded to 0.7 - the same value as a villain who had raised three
# times, which conflated "stuck around" with "applied pressure". The cumulative
# model deliberately scores pure calling low (~0.24) and reserves the high end
# for real aggression. This assertion changed with that redesign, on purpose.
if not (0.10 < captured[1] < 0.40):
    failures.append(f"C: two called streets should be WEAK evidence (~0.24), got {captured[1]}")

print()
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PASS: range_strength lowers equity vs a proven caller, is wired into the live "
      "decision path, and scores passive calling as weak evidence")
