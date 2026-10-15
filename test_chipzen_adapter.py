"""Tests for chipzen_adapter: Chipzen GameState -> decide_live -> legal Action.

    py test_chipzen_adapter.py
"""
import random

from chipzen import GameState

import chipzen_adapter as A

HU = {"small_blind": 5, "big_blind": 10, "num_players": 2, "starting_stack": 1000}
SIX = {"small_blind": 5, "big_blind": 10, "num_players": 6, "starting_stack": 1000}


def gs(**kw):
    msg = {"valid_actions": kw.pop("valid"), "request_id": "r1", "round_id": "rd1", "state": kw}
    return GameState.from_turn_request(msg)


def legal(action, state):
    assert action.action in state.valid_actions, (action, state.valid_actions)
    if action.action == "raise":
        assert state.min_raise <= action.amount <= state.max_raise, (action, state.min_raise, state.max_raise)


# 1. Heads-up preflop, facing a raise, premium hand -> continues, legal.
s = gs(hand_number=1, phase="preflop", your_hole_cards=["Ah", "Ad"], board=[], pot=40,
       your_stack=990, opponent_stacks=[970], your_seat=1, dealer_seat=0, to_call=20,
       min_raise=50, max_raise=1000, valid=["fold", "call", "raise"],
       action_history=[
           {"seat": 0, "action": "post_small_blind", "amount": 5, "phase": "preflop", "is_timeout": False},
           {"seat": 1, "action": "post_big_blind", "amount": 10, "phase": "preflop", "is_timeout": False},
           {"seat": 0, "action": "raise", "amount": 30, "phase": "preflop", "is_timeout": False}])
t = A.to_devfun_table(s, HU)
me = [x for x in t["seats"] if x["seatNumber"] == t["selfSeatNumber"]][0]
assert me["holeCards"] == ["Ah", "Ad"] and me["currentBetChips"] == 10
assert t["currentBet"] == 30 and t["allowedActions"]["callToAmount"] == 30
assert t["allowedActions"]["canRaise"] and not t["allowedActions"]["canBet"]
act, dec = A.decide_chipzen(s, HU)
legal(act, s)
assert act.action in ("call", "raise"), f"AA folded? {dec}"
print("1 ok  AA vs raise ->", act.to_wire(), dec.get("_debug", {}).get("decided_by"))

# 2. Postflop, checked to us -> opening action must translate as 'bet' internally.
s = gs(hand_number=2, phase="flop", your_hole_cards=["7c", "2d"], board=["Ks", "Qh", "9s"], pot=60,
       your_stack=970, opponent_stacks=[970], your_seat=0, dealer_seat=0, to_call=0,
       min_raise=10, max_raise=970, valid=["check", "raise"],
       action_history=[
           {"seat": 0, "action": "post_small_blind", "amount": 5, "phase": "preflop", "is_timeout": False},
           {"seat": 1, "action": "post_big_blind", "amount": 10, "phase": "preflop", "is_timeout": False},
           {"seat": 0, "action": "raise", "amount": 30, "phase": "preflop", "is_timeout": False},
           {"seat": 1, "action": "call", "amount": 20, "phase": "preflop", "is_timeout": False},
           {"seat": 1, "action": "check", "amount": 0, "phase": "flop", "is_timeout": False}])
t = A.to_devfun_table(s, HU)
assert t["street"] == "Flop" and t["currentBet"] == 0
assert t["allowedActions"]["canBet"] and "bet" in t["allowedActions"]["availableActions"]
act, dec = A.decide_chipzen(s, HU)
legal(act, s)
assert act.action != "fold", "folded a free check"
print("2 ok  72o free flop ->", act.to_wire())

# 3. Six-handed: folds and multiple seats map correctly.
s = gs(hand_number=3, phase="preflop", your_hole_cards=["Kd", "Qd"], board=[], pot=45,
       your_stack=1000, opponent_stacks=[995, 990, 1000, 970, 1000], your_seat=5, dealer_seat=5,
       to_call=30, min_raise=50, max_raise=1000, valid=["fold", "call", "raise"],
       action_history=[
           {"seat": 0, "action": "post_small_blind", "amount": 5, "phase": "preflop", "is_timeout": False},
           {"seat": 1, "action": "post_big_blind", "amount": 10, "phase": "preflop", "is_timeout": False},
           {"seat": 2, "action": "fold", "amount": 0, "phase": "preflop", "is_timeout": False},
           {"seat": 3, "action": "raise", "amount": 30, "phase": "preflop", "is_timeout": False},
           {"seat": 4, "action": "fold", "amount": 0, "phase": "preflop", "is_timeout": True}])
t = A.to_devfun_table(s, SIX)
st = {x["seatNumber"]: x["status"] for x in t["seats"]}
assert st[3] == "Folded" and st[5] == "Folded" and st[4] == "Active", st
assert len(t["seats"]) == 6
act, dec = A.decide_chipzen(s, SIX)
legal(act, s)
print("3 ok  KQs 6-max on button ->", act.to_wire())

# 4. Fuzz: random legal-looking states never produce an illegal action or crash.
deck = [r + su for r in "23456789TJQKA" for su in "cdhs"]
random.seed(7)
for i in range(300):
    random.shuffle(deck)
    phase = random.choice(["preflop", "flop", "turn", "river"])
    nb = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}[phase]
    to_call = random.choice([0, 0, 10, 40, 200, 900])
    stack = random.randint(50, 2000)
    valid = (["check"] if to_call == 0 else ["fold", "call"]) + (["raise"] if stack > to_call else [])
    s = gs(hand_number=i, phase=phase, your_hole_cards=deck[:2], board=deck[2:2 + nb], pot=random.randint(15, 1500),
           your_stack=stack, opponent_stacks=[random.randint(0, 2000)], your_seat=i % 2, dealer_seat=0,
           to_call=to_call, min_raise=to_call + 10, max_raise=stack, valid=valid,
           action_history=[{"seat": 0, "action": "post_small_blind", "amount": 5, "phase": "preflop", "is_timeout": False},
                           {"seat": 1, "action": "post_big_blind", "amount": 10, "phase": "preflop", "is_timeout": False}])
    act, dec = A.decide_chipzen(s, HU)
    legal(act, s)
    if to_call == 0:
        assert act.action != "fold"
print("4 ok  300 fuzzed states, all legal, never folded a free check")
print("ALL PASS")
