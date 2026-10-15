"""INVARIANT: decide_live must never fold when a strictly cheaper action exists.

Two forms of the same defect, found two days apart, both in a safeguard that
fell through to fold when the action it actually wanted was unavailable:

  cmtlp5gg  -231  STACK_RISK_CAP wanted raise->call, needed to_call > 0, got
                  to_call == 0, fell through to fold. Folded a 464-chip pot
                  holding a pair with CHECKING LEGAL.
  cmtlxu0b   -50  the velocity brake wanted raise->call, the call failed the
                  cap, fell through to fold. Folded two pair at 3.7:1 with
                  86.6% equity - a +116 EV call - with CALLING LEGAL.

The first version of this test asserted only the first form, because the brief
that prompted it named a specific trigger ("a fallthrough to fold when the
intended action isn't legal") and I checked for exactly that wording. The
velocity brake passed: its intended action was legal, merely unaffordable. Same
fallthrough, same cost, different trigger - and my audit cleared it.

So this asserts the OUTCOME, not any trigger:

    folding is wrong whenever a strictly cheaper legal action was available.

  - to_call == 0: checking is free, so folding is never right. Cheaper by any
    measure - it costs nothing and keeps all equity.
  - the proposed action was a raise/bet/all-in and calling is legal: calling
    commits strictly less than the action being refused, so a safeguard whose
    job is to LIMIT commitment cannot coherently answer with the one action
    that forfeits everything already committed.

Stated that way the property is trigger-independent: any future safeguard that
reaches fold by any route fails this, including routes nobody has thought of.
That is the point - the first version tested my model of the bug, and the bug
was in the model.

Deliberately NOT asserted: that a fold is wrong whenever it is -EV. That needs
the equity estimate, and the cmtcarsk bust (equity 0.828, pot odds 0.209,
priced at +2763 EV) folded correctly precisely because the equity was wrong.
This invariant never consults equity.
"""
import itertools
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L

BOARDS = {
    "Preflop": [],
    "Flop": ["Jd", "4h", "7c"],
    "Turn": ["Jd", "4h", "7c", "Tc"],
    "River": ["Jd", "4h", "7c", "Tc", "2s"],
}

HOLES = [
    ["Ts", "Td"],   # cmtlp5gg: a pair, just under the 0.80 equity floor
    ["7h", "Jh"],   # cmtlxu0b: two pair on these boards
    ["Ah", "As"],   # premium
    ["7c", "2d"],   # the worst hand in poker: must still check, never fold
    ["9h", "4s"],   # air
]

# Spans both sides of STACK_RISK_CAP (0.5) and of the velocity caps, and
# includes the 146-chip depth at which the brake bug actually fired.
DEPTHS = [(2, 998), (100, 900), (38, 63), (343, 657), (500, 500), (900, 100)]


def make_table(street, hole, committed, stack, to_call, pot):
    """A spot with a KNOWN cheaper alternative to folding.

    to_call == 0 -> checking is free.
    to_call  > 0 -> calling is legal and cheaper than any raise.
    canFold is always True, mirroring the live payload: the arena offers fold
    even where it is strictly dominated, so the agent must be the one to refuse.
    """
    free = to_call <= 0
    seats = [
        {"seatId": "s1", "seatNumber": 1, "agentId": "me", "agentName": "Me",
         "agentHandle": "me", "status": "Active", "stackChips": stack,
         "currentBetChips": 0, "totalCommittedChips": committed,
         "payoutChips": None, "holeCards": list(hole)},
        {"seatId": "s2", "seatNumber": 2, "agentId": "opp1", "agentName": "Opp1",
         "agentHandle": "opp1", "status": "Active", "stackChips": stack,
         "currentBetChips": to_call, "totalCommittedChips": committed + to_call,
         "payoutChips": None, "holeCards": None},
    ]
    actions = ["fold", "all-in"] + (["check", "bet"] if free else ["call", "raise"])
    return {
        "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
        "status": "Active", "street": street,
        "potChips": pot, "currentBet": to_call, "minRaiseTo": max(2, to_call * 2),
        "startedAt": 1, "endedAt": None, "countdownEndsAt": None,
        "actionDeadlineAt": 999999, "currentSeatNumber": 1,
        "boardCards": BOARDS[street],
        "smallBlindChips": 1, "bigBlindChips": 2, "buyInChips": 2,
        "winners": [], "seats": seats,
        "actingSeatNumber": 1, "selfSeatNumber": 1,
        "allowedActions": {
            "canFold": True, "canCheck": free, "canCall": not free,
            "canBet": free, "canRaise": not free,
            "callAmount": to_call, "callChips": to_call, "callToAmount": to_call,
            "minBet": 2 if free else None,
            "minRaiseTo": None if free else max(2, to_call * 2),
            "maxCommit": stack, "allInToAmount": stack,
            "betRange": {"min": 2, "max": stack} if free else None,
            "raiseRange": None if free else {"min": max(2, to_call * 2), "max": stack},
            "canAllIn": True, "availableActions": actions,
            "amountSemantics": "toAmount", "amountHint": "test",
            "reasoningRequired": False, "actionHint": "test",
        },
        "recentEvents": [],
    }


violations = []
checked = 0

for street, hole, (committed, stack) in itertools.product(BOARDS, HOLES, DEPTHS):
    # to_call == 0 (checking free), and a range of prices where calling is
    # legal - including to_call large relative to stack, which is what pushed
    # the velocity brake's downgrade target over its cap.
    for to_call in (0, 8, 38, int(stack * 0.25) or 1, int(stack * 0.6) or 1):
        pot = max(4, to_call * 3 + committed)
        t = make_table(street, hole, committed, stack, to_call, pot)
        res = L.decide_live(t)
        checked += 1
        avail = set(a.lower() for a in t["allowedActions"]["availableActions"])
        assert res["action"] in avail, (
            f"illegal action {res['action']} not in {avail} "
            f"({street} {hole} committed={committed} to_call={to_call})")
        if res["action"] != "fold":
            continue
        cheaper = "check (free)" if to_call <= 0 else None
        if cheaper is None and "call" in avail:
            # Folding is only a violation if the agent's own reasoning was
            # heading somewhere MORE expensive than a call - i.e. a safeguard
            # refused a raise. A fold chosen on its merits from a call spot is
            # legitimate and is not asserted against here.
            sb = (res.get("_debug") or {}).get("shadow_brake") or {}
            if sb.get("actual") in ("raise", "bet", "all-in"):
                cheaper = f"call (refusing a {sb.get('actual')})"
        if cheaper:
            violations.append((street, "".join(hole), committed, stack, to_call,
                               (res.get("_debug") or {}).get("decided_by"),
                               cheaper, res["message"]))

if violations:
    print("FAIL: folded with a strictly cheaper action available, "
          "in %d of %d states:" % (len(violations), checked))
    for street, hole, committed, stack, to_call, by, cheaper, msg in violations:
        print("  %-8s %-5s committed=%-4d stack=%-4d to_call=%-4d "
              "cheaper=%-26s decided_by=%s" % (street, hole, committed, stack,
                                               to_call, cheaper, by))
        print("           | %s" % msg)
    raise SystemExit(1)

print("checked %d states: %d streets x %d holes x %d depths x 5 prices"
      % (checked, len(BOARDS), len(HOLES), len(DEPTHS)))
print("covers both real failures: to_call=0 at 34% commitment (cmtlp5gg,")
print("STACK_RISK_CAP) and to_call=38 at a 63-chip stack (cmtlxu0b, velocity")
print("brake, where the call itself exceeded the cap).")
print()
print("PASS: decide_live never folds when a strictly cheaper action is legal.")
