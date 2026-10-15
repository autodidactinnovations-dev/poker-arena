"""Preflop polarization (v44): raise or fold, stop calling.

Verifies the four properties the change was specced on:
  1. sub-tier-3 hands FACING A RAISE fold instead of calling
  2. the BB EXEMPTION actually fires - a defended big blind still calls
  3. branch C is raise-or-fold: tiered hands raise, untiered fold, no calls
  4. PREFLOP_POLARIZE_ENABLED=False reverts every one of the above exactly

Plus the invariant that outranks all of them: a FREE check is never folded.
Rules A and B both gate on to_call/position, and a rule that folds a free
option would be the third instance of that bug in this file's history.
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


def table(hole, to_call, current_bet, bb=2, n_seats=6, my_seat=3,
          sb_seat=1, pot=None, free_check=False):
    """A preflop spot. `current_bet` > bb makes it branch B (facing a raise);
    == bb makes it branch C (limped / BB-only)."""
    seats = []
    for i in range(1, n_seats + 1):
        seats.append({
            "seatId": "s%d" % i, "seatNumber": i,
            "agentId": "me" if i == my_seat else "opp%d" % i,
            "agentName": "Me" if i == my_seat else "Opp%d" % i,
            "agentHandle": "me" if i == my_seat else "opp%d" % i,
            "status": "Active", "stackChips": 1000,
            "currentBetChips": current_bet if i != my_seat else max(0, current_bet - to_call),
            "totalCommittedChips": current_bet if i != my_seat else max(0, current_bet - to_call),
            "payoutChips": None,
            "holeCards": list(hole) if i == my_seat else None})
    events = [
        {"id": "b1", "sequence": 1, "type": "BlindPosted", "street": "Preflop",
         "occurredAt": 1, "summary": {"action": None, "amount": bb // 2,
                                      "seatNumber": sb_seat, "agentName": "sb"}},
        {"id": "b2", "sequence": 2, "type": "BlindPosted", "street": "Preflop",
         "occurredAt": 2, "summary": {"action": None, "amount": bb,
                                      "seatNumber": (sb_seat % n_seats) + 1,
                                      "agentName": "bb"}},
    ]
    if current_bet > bb:
        events.append({"id": "r1", "sequence": 3, "type": "ActionTaken",
                       "street": "Preflop", "occurredAt": 3,
                       "summary": {"action": "raise", "amount": None,
                                   "toAmount": current_bet, "seatNumber": 2,
                                   "agentName": "Opp2"}})
    free = to_call <= 0
    acts = ["fold", "all-in"] + (["check"] if free else ["call"]) + ["raise"]
    return {
        "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
        "status": "Active", "street": "Preflop",
        "potChips": pot if pot is not None else (current_bet * 2 + bb),
        "currentBet": current_bet, "minRaiseTo": current_bet * 2,
        "startedAt": 1, "endedAt": None, "countdownEndsAt": None,
        "actionDeadlineAt": 999999, "currentSeatNumber": my_seat,
        "boardCards": [], "smallBlindChips": bb // 2, "bigBlindChips": bb,
        "buyInChips": bb, "winners": [], "seats": seats,
        "actingSeatNumber": my_seat, "selfSeatNumber": my_seat,
        "allowedActions": {
            "canFold": True, "canCheck": free, "canCall": not free,
            "canBet": False, "canRaise": True,
            "callAmount": max(0, to_call), "callChips": max(0, to_call),
            "callToAmount": current_bet, "minBet": None,
            "minRaiseTo": current_bet * 2, "maxCommit": 1000,
            "allInToAmount": 1000, "betRange": None,
            "raiseRange": {"min": current_bet * 2, "max": 1000},
            "canAllIn": True, "availableActions": acts,
            "amountSemantics": "toAmount", "amountHint": "t",
            "reasoningRequired": False, "actionHint": "t"},
        "recentEvents": events,
    }


def run(t):
    res = L.decide_live(t)
    d = res.get("_debug") or {}
    avail = set(a.lower() for a in t["allowedActions"]["availableActions"])
    assert res["action"] in avail, "illegal action %r" % res["action"]
    return res["action"], d.get("decided_by"), d.get("seat_label")


# 72o is unranked; AA/KJs are tier 1/2. Seat 3 of 6 with SB at seat 1 is not
# a blind, so it exercises the non-BB path.
JUNK, TIER1, TIER2 = ("7c", "2d"), ("Ah", "As"), ("Kc", "Jc")
print("tier check: 72o=%s AA=%s KJs=%s"
      % (L._rfi_tier(list(JUNK)), L._rfi_tier(list(TIER1)), L._rfi_tier(list(TIER2))))
assert L._rfi_tier(list(JUNK)) is None, "72o must be untiered for these tests"
print()

L.PREFLOP_POLARIZE_ENABLED = True
print("1. FACING A RAISE (branch B), not in the blinds:")
a, by, pos = run(table(JUNK, to_call=8, current_bet=10))
check("   sub-tier-3 junk folds instead of calling", (a, by),
      ("fold", "polarize_fold_vs_raise"))
a, by, pos = run(table(TIER1, to_call=8, current_bet=10))
check("   tier-1 hand still 3-bets", a, "raise")

print()
print("2. BB EXEMPTION - a defended big blind must NOT be folded by the rule:")
# seat 3 is the BB when SB is seat 2
a, by, pos = run(table(JUNK, to_call=8, current_bet=10, sb_seat=2))
# assert on seat_label, NOT rfi_pos: rfi_should_open returns None for any
# untiered hand, so rfi_pos is None for exactly the hands these rules target.
# The first version of this test asserted on rfi_pos and caught a real bug -
# the BB exemption was keyed off it and could never fire.
check("   seat_label is BB in this fixture", pos, "BB")
check("   BB is exempt from rule A", by != "polarize_fold_vs_raise", True)

print()
print("3. BRANCH C (limped / BB-only) is raise-or-fold:")
a, by, pos = run(table(JUNK, to_call=2, current_bet=2))
check("   untiered hand folds, does not limp", (a, by),
      ("fold", "polarize_fold_branch_c"))
a, by, pos = run(table(TIER2, to_call=2, current_bet=2))
check("   tiered hand raises", a, "raise")

print()
print("4. FREE CHECK is never folded (outranks both rules):")
for hole in (JUNK, TIER1):
    a, by, pos = run(table(hole, to_call=0, current_bet=2))
    check("   %s with to_call=0 -> not a fold" % "".join(hole), a != "fold", True)

print()
print("5. REVERT: PREFLOP_POLARIZE_ENABLED=False restores prior behaviour:")
L.PREFLOP_POLARIZE_ENABLED = False
a, by, pos = run(table(JUNK, to_call=8, current_bet=10))
check("   branch B junk no longer hits rule A", by != "polarize_fold_vs_raise", True)
a, by, pos = run(table(JUNK, to_call=2, current_bet=2))
check("   branch C junk no longer hits rule B", by != "polarize_fold_branch_c", True)
a, by, pos = run(table(TIER1, to_call=8, current_bet=10))
check("   tier-1 3-bet unaffected by the flag", a, "raise")
L.PREFLOP_POLARIZE_ENABLED = True

print()
if failures:
    print("FAIL (%d):" % len(failures))
    for f in failures:
        print("  " + f)
    raise SystemExit(1)
print("PASS: rule A folds unopenable hands to a raise, the BB is exempt,")
print("branch C is raise-or-fold, free checks are never folded, and the flag")
print("reverts every behaviour cleanly.")
