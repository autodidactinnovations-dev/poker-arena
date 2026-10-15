import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import strategy as S

seats = [
    {"seatId": "s1", "seatNumber": 1, "agentId": "me", "agentName": "Me", "agentHandle": "me",
     "status": "Active", "stackChips": 500, "currentBetChips": 2, "totalCommittedChips": 2,
     "payoutChips": None, "holeCards": ["Ah", "Kh"]},
    {"seatId": "s2", "seatNumber": 2, "agentId": "opp1", "agentName": "Opp1", "agentHandle": "opp1",
     "status": "Active", "stackChips": 500, "currentBetChips": 5, "totalCommittedChips": 5,
     "payoutChips": None, "holeCards": None},
    {"seatId": "s3", "seatNumber": 3, "agentId": "opp2", "agentName": "Opp2", "agentHandle": "opp2",
     "status": "Folded", "stackChips": 500, "currentBetChips": 0, "totalCommittedChips": 0,
     "payoutChips": None, "holeCards": None},
]

events = [
    {"id": "e1", "sequence": 1, "type": "BlindPosted", "street": "Preflop", "occurredAt": 1,
     "summary": {"action": None, "amount": 1, "toAmount": None, "reasoning": None, "cards": None,
                 "boardCards": None, "seatNumber": 1, "agentName": "Me"}},
    {"id": "e2", "sequence": 2, "type": "BlindPosted", "street": "Preflop", "occurredAt": 2,
     "summary": {"action": None, "amount": 2, "toAmount": None, "reasoning": None, "cards": None,
                 "boardCards": None, "seatNumber": 2, "agentName": "Opp1"}},
    {"id": "e3", "sequence": 3, "type": "ActionTaken", "street": "Preflop", "occurredAt": 3,
     "summary": {"action": "raise", "amount": None, "toAmount": 10, "reasoning": None, "cards": None,
                 "boardCards": None, "seatNumber": 2, "agentName": "Opp1"}},
    {"id": "e4", "sequence": 4, "type": "TimeoutAction", "street": "Preflop", "occurredAt": 4,
     "summary": {"action": "fold", "amount": None, "toAmount": None, "reasoning": None, "cards": None,
                 "boardCards": None, "seatNumber": 3, "agentName": "Opp2"}},
    {"id": "e5", "sequence": 5, "type": "ActionTaken", "street": "Flop", "occurredAt": 5,
     "summary": {"action": "bet", "amount": 20, "toAmount": 20, "reasoning": None, "cards": None,
                 "boardCards": ["2h", "7c", "9s"], "seatNumber": 2, "agentName": "Opp1"}},
]

table = {
    "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
    "status": "Active", "street": "Flop",
    "potChips": 44, "currentBet": 20, "minRaiseTo": 40,
    "startedAt": 1, "endedAt": None, "countdownEndsAt": None, "actionDeadlineAt": 999999,
    "currentSeatNumber": 1, "boardCards": ["2h", "7c", "9s"],
    "smallBlindChips": 1, "bigBlindChips": 2, "buyInChips": 2,
    "winners": [], "seats": seats,
    "actingSeatNumber": 1, "selfSeatNumber": 1,
    "allowedActions": {
        "canFold": True, "canCheck": False, "canCall": True, "canBet": False, "canRaise": True,
        "callAmount": 20, "callChips": 20, "callToAmount": 20,
        "minBet": None, "minRaiseTo": 40, "maxCommit": 498, "allInToAmount": 498,
        "betRange": None, "raiseRange": {"min": 40, "max": 498}, "canAllIn": True,
        "availableActions": ["fold", "call", "raise", "all-in"],
        "amountSemantics": "toAmount", "amountHint": "test",
        "reasoningRequired": False, "actionHint": "test",
    },
    "recentEvents": events,
}

# call decide() 3 times with growing recentEvents lists (simulating repeated polls) to
# check idempotent dedup, then verify opp1's stats accumulated exactly once per event.
for _ in range(3):
    res = S.decide(table)
    print("decide ->", res)

assert "opp1" in S._OPPONENT_STATS, S._OPPONENT_STATS
stats = S._OPPONENT_STATS["opp1"]
print("opp1 stats:", stats)
assert stats["vpip"] == 1, f"expected vpip=1 (deduped across repeated polls), got {stats}"
assert stats["agg"] == 1, f"expected agg=1, got {stats}"
assert stats["passive"] == 0

looseness, aggression = S._opp_profile(["opp1"])
print("looseness:", looseness, "aggression:", aggression)
assert 0 <= looseness <= 1
assert 0 <= aggression <= 1

# opp2 folded preflop -> should not have vpip/agg/passive recorded (fold isn't tracked)
assert "opp2" not in S._OPPONENT_STATS or S._OPPONENT_STATS["opp2"] == {"vpip": 0, "agg": 0, "passive": 0}

print("ALL PASS")
