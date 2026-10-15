import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L

seats = [
    {"seatId": "s1", "seatNumber": 1, "agentId": "me", "agentName": "Me", "agentHandle": "me",
     "status": "Active", "stackChips": 500, "currentBetChips": 2, "totalCommittedChips": 2,
     "payoutChips": None, "holeCards": ["Ah", "Kh"]},
    {"seatId": "s2", "seatNumber": 2, "agentId": "opp1", "agentName": "Opp1", "agentHandle": "opp1",
     "status": "Active", "stackChips": 500, "currentBetChips": 5, "totalCommittedChips": 5,
     "payoutChips": None, "holeCards": None},
]

table = {
    "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
    "status": "Active", "street": "Preflop",
    "potChips": 7, "currentBet": 5, "minRaiseTo": 8,
    "startedAt": 1, "endedAt": None, "countdownEndsAt": None, "actionDeadlineAt": 999999,
    "currentSeatNumber": 1, "boardCards": [],
    "smallBlindChips": 1, "bigBlindChips": 2, "buyInChips": 2,
    "winners": [], "seats": seats,
    "actingSeatNumber": 1, "selfSeatNumber": 1,
    "allowedActions": {
        "canFold": True, "canCheck": False, "canCall": True, "canBet": False, "canRaise": True,
        "callAmount": 3, "callChips": 3, "callToAmount": 5,
        "minBet": None, "minRaiseTo": 8, "maxCommit": 498, "allInToAmount": 498,
        "betRange": None, "raiseRange": {"min": 8, "max": 498}, "canAllIn": True,
        "availableActions": ["fold", "call", "raise", "all-in"],
        "amountSemantics": "toAmount", "amountHint": "test",
        "reasoningRequired": True, "actionHint": "test",
    },
    "recentEvents": [
        {"id": "e1", "sequence": 1, "type": "BlindPosted", "street": "Preflop", "occurredAt": 1,
         "summary": {"action": None, "amount": 1, "toAmount": None, "reasoning": None, "cards": None,
                     "boardCards": None, "seatNumber": 1, "agentName": "Me"}},
        {"id": "e2", "sequence": 2, "type": "BlindPosted", "street": "Preflop", "occurredAt": 2,
         "summary": {"action": None, "amount": 2, "toAmount": None, "reasoning": None, "cards": None,
                     "boardCards": None, "seatNumber": 2, "agentName": "Opp1"}},
        {"id": "e3", "sequence": 3, "type": "ActionTaken", "street": "Preflop", "occurredAt": 3,
         "summary": {"action": "raise", "amount": None, "toAmount": 5, "reasoning": None, "cards": None,
                     "boardCards": None, "seatNumber": 2, "agentName": "Opp1"}},
    ],
}

res = L.decide_live(table)
print("decide_live ->", res)
avail = set(a.lower() for a in table["allowedActions"]["availableActions"])
assert res["action"] in avail, f"illegal action {res['action']} not in {avail}"
assert isinstance(res["amount"], int)
assert "message" in res and res["message"]
assert "reasoning" in res and res["reasoning"], "reasoningRequired was True but no reasoning returned"
print("ALL PASS")
