"""
DevFun Arena - Texas Hold'em static-agent strategy for competition seed_poker_eval_s1.

The sandbox harness's exact call signature isn't publicly documented, so this
module exposes several common entry-point names (decide/act/get_action/
on_decision/strategy) all backed by the same decide() function, and decide()
reads state defensively under both camelCase and snake_case key variants.
"""

import itertools
import random

RANK_VALUES = {r: v for v, r in enumerate("23456789TJQKA", start=2)}


def _parse_card(card):
    rank, suit = card[0], card[1]
    return RANK_VALUES[rank], suit


def _get(state, *names, default=None):
    for name in names:
        if isinstance(state, dict) and name in state and state[name] is not None:
            return state[name]
    return default


def _hand_rank_5(cards):
    """Return a comparable tuple rank for the best interpretation of exactly 5 cards."""
    values = sorted((v for v, _ in cards), reverse=True)
    suits = [s for _, s in cards]
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    by_count = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    is_flush = len(set(suits)) == 1

    uniq_vals = sorted(set(values), reverse=True)
    is_straight = False
    straight_high = None
    if len(uniq_vals) == 5:
        if uniq_vals[0] - uniq_vals[4] == 4:
            is_straight = True
            straight_high = uniq_vals[0]
        elif uniq_vals == [14, 5, 4, 3, 2]:
            is_straight = True
            straight_high = 5

    pattern = [c for _, c in by_count]
    ordered_vals = [v for v, _ in by_count]

    if is_straight and is_flush:
        return (8, straight_high)
    if pattern == [4, 1]:
        return (7, ordered_vals)
    if pattern == [3, 2]:
        return (6, ordered_vals)
    if is_flush:
        return (5, values)
    if is_straight:
        return (4, straight_high)
    if pattern == [3, 1, 1]:
        return (3, ordered_vals)
    if pattern == [2, 2, 1]:
        return (2, ordered_vals)
    if pattern == [2, 1, 1, 1]:
        return (1, ordered_vals)
    return (0, values)


def _best_hand_rank(cards):
    if len(cards) <= 5:
        return _hand_rank_5(cards)
    return max(_hand_rank_5(list(c)) for c in itertools.combinations(cards, 5))


def _hand_strength(hole, board):
    """Rough 0..1 strength estimate. Preflop uses a simple heuristic; postflop
    evaluates the best 5-card hand and scores it against hand-category tiers."""
    if not board:
        v1, s1 = hole[0]
        v2, s2 = hole[1]
        hi, lo = max(v1, v2), min(v1, v2)
        score = (hi + lo) / 28.0
        if v1 == v2:
            score += 0.25
        if s1 == s2:
            score += 0.06
        gap = hi - lo
        if gap == 1:
            score += 0.05
        elif gap == 2:
            score += 0.02
        return max(0.0, min(1.0, score))

    all_cards = hole + board
    category, _ = _best_hand_rank(all_cards)
    return min(1.0, category / 8.0 + 0.05)


def decide(state):
    hole_raw = _get(state, "holeCards", "hole_cards", "cards", "hand", default=[])
    board_raw = _get(state, "board", "communityCards", "community_cards", "boardCards", default=[])
    allowed = _get(state, "allowedActions", "allowed_actions", "legalActions", "legal_actions", default=None)
    pot = _get(state, "pot", "potSize", "pot_size", default=0) or 0
    to_call = _get(state, "toCall", "to_call", "callAmount", "call_amount", default=0) or 0
    current_bet = _get(state, "currentBet", "current_bet", default=None)
    my_bet = _get(state, "myBet", "my_bet", "betAmount", default=0) or 0
    stack = _get(state, "stack", "chips", "myStack", "my_stack", "tableChips", default=None)
    min_raise = _get(state, "minRaise", "min_raise", default=None)
    max_raise = _get(state, "maxRaise", "max_raise", default=None)

    if current_bet is not None and to_call == 0:
        try:
            to_call = max(0, int(current_bet) - int(my_bet))
        except (TypeError, ValueError):
            pass

    try:
        hole = [_parse_card(c) for c in hole_raw]
        board = [_parse_card(c) for c in board_raw]
    except Exception:
        hole, board = [], []

    strength = _hand_strength(hole, board) if len(hole) == 2 else 0.3

    allowed_names = set()
    range_by_action = {}
    if isinstance(allowed, list):
        for a in allowed:
            if isinstance(a, dict):
                name = a.get("action") or a.get("name") or a.get("type")
                if name:
                    allowed_names.add(name.lower())
                    range_by_action[name.lower()] = a
            elif isinstance(a, str):
                allowed_names.add(a.lower())

    def has(name):
        return not allowed_names or name in allowed_names

    def raise_to_amount(fraction):
        target = int((my_bet + to_call) + max(pot, 10) * fraction)
        info = range_by_action.get("raise") or range_by_action.get("bet") or {}
        lo = info.get("min") or info.get("minAmount") or min_raise
        hi = info.get("max") or info.get("maxAmount") or max_raise or stack
        if lo is not None:
            target = max(target, int(lo))
        if hi is not None:
            target = min(target, int(hi))
        if stack is not None:
            target = min(target, int(stack))
        return max(target, 1)

    pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0.0

    if to_call <= 0:
        if strength >= 0.62 and (has("raise") or has("bet")):
            action = "raise" if has("raise") else "bet"
            amount = raise_to_amount(0.66 if strength > 0.8 else 0.45)
            msg = "board looks good for me, betting for value"
        elif has("check"):
            action, amount, msg = "check", 0, "nothing worth building the pot for yet"
        elif has("call"):
            action, amount, msg = "call", 0, "checking is off the table, taking the free option"
        else:
            action, amount, msg = ("fold", 0, "no cheap way to see another card") if has("fold") else (next(iter(allowed_names), "check"), 0, "taking the only line available")
    else:
        if strength >= 0.7 and (has("raise") or has("bet")):
            action = "raise" if has("raise") else "bet"
            amount = raise_to_amount(0.75)
            msg = "strong enough to push here, applying pressure"
        elif strength >= pot_odds + 0.08 and has("call"):
            action, amount, msg = "call", to_call, "price is right for this equity, calling"
        elif has("fold"):
            action, amount, msg = "fold", 0, "not enough hand to justify the cost"
        elif has("check"):
            action, amount, msg = "check", 0, "taking the free card since folding isn't better"
        else:
            action, amount, msg = next(iter(allowed_names), "call"), to_call, "playing the only line available"

    if random.random() < 0.02 and has("fold") and to_call > 0 and action != "fold":
        pass

    return {"action": action, "amount": int(amount), "message": msg, "reasoning": msg}


def act(state):
    return decide(state)


def get_action(state):
    return decide(state)


def on_decision(state):
    return decide(state)


def strategy(state):
    return decide(state)
