"""The Stage 1 3-bet gate must fire on a FIRST re-raise and never re-fire.

v35 lost 854 of 1000 chips in 45 hands. -851 of that came from five hands, and
-623 from three: cmtloxo0 (-309), cmtlp5gg (-231), cmtluq1d (-83). All three had
the same shape - we 3-bet, the opponent 4-bet, and we 5-bet into it, then a
circuit breaker fired with the money already committed and we folded.

The cause was that the gate at live_agent.py:1094 tested ONLY card class:

    if (STAGE1_RFI_ENABLED and _t is not None and _t <= 2 and has("raise")):

`raise_thresh` is computed on the line directly above and never consulted on
this path. All 8 value_3bet firings in v35 were below their own raise_thresh.
More importantly the gate had no re-raise counter, so a tier-1/2 hand re-raised
indefinitely: escalation_bump - which exists precisely to make re-raising harder
as a war escalates, and whose docstring in strategy.py cites the round-51 bust
as the failure mode it was built to catch - was computed and then discarded.

The fix adds `opp_raises <= 1`. Deliberately NOT `strength >= raise_thresh`:
that would also suppress opening 3-bets and confound the Stage 1 PFR
measurement, which is the live experiment. This cap only stops re-fires.

What these tests pin:
  1. a first 3-bet (opp_raises == 1) still fires, tier 1 and tier 2
  2. a re-raise (opp_raises >= 2) does NOT take the value_3bet path
  3. (2) holds REGARDLESS OF TIER - cmtlp5gg's blocked 77 was TT, a tier-1
     hand, so a tier-only reading of the rule would have let it through
  4. blocking the re-raise does not produce an illegal action
"""
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L
import strategy as S


def make_table(hole, opp_raises, current_bet, pot, my_committed=2):
    """A preflop spot with `opp_raises` opponent raises already on this street.

    Seat 1 is us. Events are built so _opponent_raises_this_street counts
    exactly `opp_raises`, which is the input the cap keys on.
    """
    events = [
        {"id": "b1", "sequence": 1, "type": "BlindPosted", "street": "Preflop", "occurredAt": 1,
         "summary": {"action": None, "amount": 1, "toAmount": None, "seatNumber": 1,
                     "agentName": "Me"}},
        {"id": "b2", "sequence": 2, "type": "BlindPosted", "street": "Preflop", "occurredAt": 2,
         "summary": {"action": None, "amount": 2, "toAmount": None, "seatNumber": 2,
                     "agentName": "Opp1"}},
    ]
    for i in range(opp_raises):
        events.append(
            {"id": f"r{i}", "sequence": 3 + i, "type": "ActionTaken", "street": "Preflop",
             "occurredAt": 3 + i,
             "summary": {"action": "raise", "amount": None, "toAmount": current_bet,
                         "seatNumber": 2, "agentName": "Opp1"}})

    seats = [
        {"seatId": "s1", "seatNumber": 1, "agentId": "me", "agentName": "Me", "agentHandle": "me",
         "status": "Active", "stackChips": 1000 - my_committed, "currentBetChips": my_committed,
         "totalCommittedChips": my_committed, "payoutChips": None, "holeCards": list(hole)},
        {"seatId": "s2", "seatNumber": 2, "agentId": "opp1", "agentName": "Opp1",
         "agentHandle": "opp1", "status": "Active", "stackChips": 1000 - current_bet,
         "currentBetChips": current_bet, "totalCommittedChips": current_bet,
         "payoutChips": None, "holeCards": None},
    ]
    to_call = current_bet - my_committed
    return {
        "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
        "status": "Active", "street": "Preflop",
        "potChips": pot, "currentBet": current_bet, "minRaiseTo": current_bet * 2,
        "startedAt": 1, "endedAt": None, "countdownEndsAt": None, "actionDeadlineAt": 999999,
        "currentSeatNumber": 1, "boardCards": [],
        "smallBlindChips": 1, "bigBlindChips": 2, "buyInChips": 2,
        "winners": [], "seats": seats,
        "actingSeatNumber": 1, "selfSeatNumber": 1,
        "allowedActions": {
            "canFold": True, "canCheck": False, "canCall": True, "canBet": False,
            "canRaise": True, "callAmount": to_call, "callChips": to_call,
            "callToAmount": current_bet, "minBet": None, "minRaiseTo": current_bet * 2,
            "maxCommit": 1000 - my_committed, "allInToAmount": 1000 - my_committed,
            "betRange": None, "raiseRange": {"min": current_bet * 2, "max": 1000 - my_committed},
            "canAllIn": True,
            "availableActions": ["fold", "call", "raise", "all-in"],
            "amountSemantics": "toAmount", "amountHint": "test",
            "reasoningRequired": False, "actionHint": "test",
        },
        "recentEvents": events,
    }


def run(hole, opp_raises, current_bet, pot, label):
    t = make_table(hole, opp_raises, current_bet, pot)
    counted = S._opponent_raises_this_street(t, 1)
    assert counted == opp_raises, \
        f"{label}: fixture built {counted} opponent raises, expected {opp_raises}"
    res = L.decide_live(t)
    dbg = res.get("_debug") or {}
    avail = set(a.lower() for a in t["allowedActions"]["availableActions"])
    assert res["action"] in avail, f"{label}: illegal action {res['action']} not in {avail}"
    print("  %-46s tier=%s oppR=%s -> %-6s decided_by=%s"
          % (label, L._rfi_tier(list(hole)), counted, res["action"], dbg.get("decided_by")))
    return res, dbg


print("FIRST 3-bet (opp_raises == 1) must still fire - the cap must not")
print("suppress opening aggression, which is the Stage 1 measurement:")
_, d = run(("Ts", "Td"), 1, 8, 12, "TT tier 1, first re-raise")
assert d.get("decided_by") == "value_3bet_tier", "tier-1 first 3-bet stopped firing"
_, d = run(("Jc", "Kc"), 1, 8, 12, "KJs tier 2, first re-raise")
assert d.get("decided_by") == "value_3bet_tier", "tier-2 first 3-bet stopped firing"
_, d = run(("Jd", "Ac"), 1, 8, 12, "AJo tier 2, first re-raise")
assert d.get("decided_by") == "value_3bet_tier", "tier-2 first 3-bet stopped firing"

print()
print("RE-RAISE (opp_raises >= 2) must NOT take the TIER path. These are")
print("the three v35 hands, at the escalated decision point:")
_, d = run(("Jc", "Kc"), 2, 60, 68, "cmtloxo0 -309: KJs, was raising to 104")
assert d.get("decided_by") != "value_3bet_tier", "KJs still took the tier path"
_, d = run(("Ts", "Td"), 2, 50, 50, "cmtlp5gg -231: TT, was raising to 77")
assert d.get("decided_by") != "value_3bet_tier", "TT still took the tier path"
_, d = run(("Jd", "Ac"), 2, 40, 48, "cmtluq1d  -83: AJo, was raising to 83")
assert d.get("decided_by") != "value_3bet_tier", "AJo still took the tier path"

print()
print("The counter blocks the tier path REGARDLESS OF TIER. cmtlp5gg's")
print("blocked raise was TT - a tier-1 hand - so this is not a tier rule.")
print("A hand may STILL re-raise here, but only by CLEARING the escalated")
print("raise_thresh on merit (decided_by=value_3bet). That path is correct")
print("and is what the cap restores; AA re-raising a 4-bet war is fine,")
print("KJs bypassing the threshold to do it is the bug:")
for hole, name in ((("Ah", "Ad"), "AA  tier 1"), (("Ks", "Kd"), "KK  tier 1"),
                   (("Ah", "Ks"), "AKo tier 1"), (("Ac", "Tc"), "ATs tier 2")):
    t = L._rfi_tier(list(hole))
    assert t is not None and t <= 2, f"{name} should be tier 1-2, got {t}"
    _, d = run(hole, 3, 100, 120, f"{name}, third re-raise")
    assert d.get("decided_by") != "value_3bet_tier", f"{name} took the tier path"
    if d.get("decided_by") == "value_3bet":
        th = (d.get("thresholds") or {}).get("raise_thresh")
        print("      ^ re-raised on MERIT: cleared escalated raise_thresh=%s" % th)

print()
print("Escalation still deepens as the war continues (escalation_bump is")
print("computed on this path). It is NOT yet fed to the gate - flagged")
print("separately, deliberately not changed in the same commit:")
for n in (1, 2, 3):
    t = make_table(("Ts", "Td"), n, 8 * n, 12 * n)
    res = L.decide_live(t)
    d = res.get("_debug") or {}
    print("  opp_raises=%s -> escalation_bump=%s raise_thresh=%s decided_by=%s"
          % (n, d.get("escalation_bump"),
             (d.get("thresholds") or {}).get("raise_thresh"), d.get("decided_by")))

print()
print("PASS: first 3-bet fires at tiers 1-2, re-raises never take the TIER")
print("path at any tier (they must clear raise_thresh on merit instead),")
print("and no blocked spot produces an illegal action.")
