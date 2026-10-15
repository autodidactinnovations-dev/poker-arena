"""AGGRESSION_FOLD (S18). Proves the rule's SCOPE, not just that it fires.

The scope is the whole design. Three prior bugs in this project came from a
rule reaching further than its brief (the BB exemption keyed off the wrong
variable, the sizing scheme covered a subset of what was projected, the
velocity brake fell through to fold). So the assertions here are mostly
NEGATIVE: where the rule must stay silent.

  1. fires postflop on pot_odds_call at aggression >= 4.0
  2. does NOT fire below the threshold
  3. does NOT fire preflop (aggression_score is absent/unreliable there)
  4. does NOT displace a raise - strong hands still raise at high aggression
  5. does NOT fire when checking is free (to_call == 0)
  6. AGGRESSION_FOLD_ENABLED = False reverts it exactly
"""
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L

# This suite drives REAL decisions, so every firing writes a shadow record.
# Twice now it has contaminated the production shadow file with synthetic rows
# (hand_id "t1", aggression 18.0) simply by being run. Redirect before any
# decision happens - including the ones in this module's body, which execute
# on import.
L.SHADOW_PATH = "C:/Users/billy/poker-arena/_test_aggfold_shadow.jsonl"

failures = []


def check(label, got, want):
    ok = got == want
    if not ok:
        failures.append("%s: got %r want %r" % (label, got, want))
    print("  %-66s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))


def table(board, hole, to_call, n_aggro, street="Turn", bb=20, pot=200):
    """Postflop spot. `n_aggro` raise events drive aggression_score up."""
    me = 1
    seats = [{"seatId": "s%d" % i, "seatNumber": i,
              "agentId": "me" if i == me else "opp%d" % i,
              "agentName": "Me" if i == me else "Opp%d" % i,
              "agentHandle": "me" if i == me else "opp%d" % i,
              "status": "Active", "stackChips": 1000,
              "currentBetChips": 0 if i == me else to_call,
              "totalCommittedChips": 50, "payoutChips": None,
              "holeCards": list(hole) if i == me else None} for i in (1, 2)]
    events, seq = [], 0
    for st in ("Preflop", "Flop", "Turn", "River"):
        for _ in range(n_aggro):
            seq += 1
            events.append({"id": "e%d" % seq, "sequence": seq,
                           "type": "ActionTaken", "street": st, "occurredAt": seq,
                           "summary": {"action": "raise", "amount": None,
                                       "toAmount": 60, "seatNumber": 2,
                                       "agentName": "Opp2"}})
        if st == street:
            break
    free = to_call <= 0
    acts = ["fold", "all-in"] + (["check"] if free else ["call"]) + ["raise"]
    return {
        "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
        "status": "Active", "street": street, "potChips": pot,
        "currentBet": to_call, "minRaiseTo": max(2 * to_call, bb),
        "startedAt": 1, "endedAt": None, "countdownEndsAt": None,
        "actionDeadlineAt": 999999, "currentSeatNumber": me,
        "boardCards": list(board), "smallBlindChips": bb // 2,
        "bigBlindChips": bb, "buyInChips": bb, "winners": [], "seats": seats,
        "actingSeatNumber": me, "selfSeatNumber": me,
        "allowedActions": {
            "canFold": True, "canCheck": free, "canCall": not free,
            "canBet": free, "canRaise": not free,
            "callAmount": max(0, to_call), "callChips": max(0, to_call),
            "callToAmount": to_call, "minBet": bb,
            "minRaiseTo": max(2 * to_call, bb), "maxCommit": 1000,
            "allInToAmount": 1000, "betRange": {"min": bb, "max": 1000},
            "raiseRange": {"min": max(2 * to_call, bb), "max": 1000},
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
    return res["action"], d.get("decided_by"), (d.get("aggression_score") or 0.0)


# A marginal drawing hand on a dry-ish board getting a price: the pot_odds_call
# population the rule targets.
# A made-but-marginal hand (bottom pair): it reaches the pot_odds_call arm.
# NOT a busted draw - `stalled_hand_fold` is tested BEFORE this arm and would
# fold that hand first, so an air hand proves nothing about this rule.
WEAK = (["2c", "7d", "9h", "Js"], ["7s", "8s"])
AIR = (["2c", "7d", "9h", "Js"], ["5s", "6s"])
STRONG = (["Ac", "Ad", "9h", "Js"], ["As", "Ah"])

print("1. FIRES postflop on pot_odds_call at aggression >= 4")
act, by, agg = run(table(WEAK[0], WEAK[1], to_call=30, n_aggro=3))
print("     (aggression_score reached %.2f)" % agg)
check("high aggression -> fold via aggression_fold", (act, by), ("fold", "aggression_fold"))

print("2. DOES NOT fire below the threshold")
act, by, agg = run(table(WEAK[0], WEAK[1], to_call=30, n_aggro=0))
print("     (aggression_score %.2f)" % agg)
check("low aggression -> not aggression_fold", by != "aggression_fold", True)

print("3. DOES NOT fire preflop")
pre = table([], ["5s", "6s"], to_call=30, n_aggro=3, street="Preflop")
act, by, agg = run(pre)
check("preflop -> not aggression_fold", by != "aggression_fold", True)

print("4. DOES NOT displace a raise")
act, by, agg = run(table(STRONG[0], STRONG[1], to_call=30, n_aggro=3))
check("strong hand still raises at high aggression", act in ("raise", "all-in"), True)

print("5. DOES NOT fire when checking is free")
act, by, agg = run(table(WEAK[0], WEAK[1], to_call=0, n_aggro=3))
check("to_call == 0 -> never a fold", act != "fold", True)

print("6. stalled_guard KEEPS PRECEDENCE (it is tested first)")
act, by, agg = run(table(AIR[0], AIR[1], to_call=30, n_aggro=3))
check("busted draw still attributed to stalled_guard", (act, by),
      ("fold", "stalled_guard"))

print("7. FLAG REVERTS")
L.AGGRESSION_FOLD_ENABLED = False
act, by, agg = run(table(WEAK[0], WEAK[1], to_call=30, n_aggro=3))
check("disabled -> rule silent", by != "aggression_fold", True)
L.AGGRESSION_FOLD_ENABLED = True

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS: aggression_fold fires only postflop, only on the pot_odds_call")
print("path, never over a raise, never over a free check, and reverts cleanly.")
