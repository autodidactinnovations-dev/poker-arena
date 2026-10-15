"""
DevFun Arena - Texas Hold'em static-agent strategy for competition seed_poker_eval_s1.

v4: built against the REAL API schema (confirmed via GET /api/arena/__introspection
for /api/arena/texas/pending-actions), not guesses. Key facts that differ from
earlier versions:
  - allowedActions is a FLAGS OBJECT: canFold/canCheck/canCall/canBet/canRaise/
    canAllIn booleans, plus callAmount/callChips/callToAmount/minBet/minRaiseTo/
    maxCommit/allInToAmount/betRange/raiseRange/availableActions/amountSemantics/
    reasoningRequired - NOT a list of {action,min,max} dicts.
  - Per-table fields: potChips, currentBet, minRaiseTo, boardCards, seats,
    selfSeatNumber, actingSeatNumber, street.
  - Per-seat fields (in `seats`): seatNumber, stackChips, currentBetChips,
    totalCommittedChips, holeCards (null for hidden/opponent seats).
  - A wrapping object may carry a `tables` list (multiple tables); if so we use
    the table needing action (actingSeatNumber == selfSeatNumber, or the first
    entry) rather than assuming `state` itself is the table.

Decision logic: Monte Carlo equity (win probability vs random opponent hands,
simulated to a complete board), used uniformly pre- and post-flop, driven
against pot odds with tiered aggression. Any parsing/computation failure
falls back to a legal-flag-aware safe action (fold when facing a bet, check
otherwise) instead of a hardcoded action that might be illegal.
"""

import itertools
import random

RANK_VALUES = {r: v for v, r in enumerate("23456789TJQKA", start=2)}
FULL_DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]


def _parse_card(card):
    rank, suit = card[0], card[1]
    return RANK_VALUES[rank], suit


def _hand_rank_5(cards):
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
        return (7, tuple(ordered_vals))
    if pattern == [3, 2]:
        return (6, tuple(ordered_vals))
    if is_flush:
        return (5, tuple(values))
    if is_straight:
        return (4, straight_high)
    if pattern == [3, 1, 1]:
        return (3, tuple(ordered_vals))
    if pattern == [2, 2, 1]:
        return (2, tuple(ordered_vals))
    if pattern == [2, 1, 1, 1]:
        return (1, tuple(ordered_vals))
    return (0, tuple(values))


def _best_hand_rank(cards):
    if len(cards) <= 5:
        return _hand_rank_5(cards)
    return max(_hand_rank_5(list(c)) for c in itertools.combinations(cards, 5))


def estimate_equity(hole, board, num_opponents=1, trials=200):
    if len(hole) != 2:
        return 0.5

    known = set(hole) | set(board)
    deck = [_parse_card(x) for x in FULL_DECK]
    deck = [c for c in deck if c not in known]
    need_board = 5 - len(board)
    draw_count = need_board + 2 * num_opponents

    if draw_count > len(deck) or trials <= 0:
        return 0.5

    wins = 0.0
    for _ in range(trials):
        drawn = random.sample(deck, draw_count)
        extra_board = drawn[:need_board]
        opp_cards = drawn[need_board:]
        full_board = board + extra_board

        my_rank = _best_hand_rank(hole + full_board)
        opp_ranks = []
        for i in range(num_opponents):
            opp_hole = opp_cards[2 * i:2 * i + 2]
            opp_ranks.append(_best_hand_rank(opp_hole + full_board))

        best_opp = max(opp_ranks)
        if my_rank > best_opp:
            wins += 1.0
        elif my_rank == best_opp:
            tied = 1 + sum(1 for r in opp_ranks if r == my_rank)
            wins += 1.0 / tied

    return wins / trials


def _pick_table(state):
    if isinstance(state, dict) and "tables" in state and isinstance(state["tables"], list):
        tables = state["tables"]
        if not tables:
            return state
        self_ids = {state.get("selfAgentId"), state.get("agentId")}
        for t in tables:
            if isinstance(t, dict) and t.get("actingSeatNumber") is not None and \
               t.get("actingSeatNumber") == t.get("selfSeatNumber"):
                return t
        return tables[0]
    return state if isinstance(state, dict) else {}


def _find_self_seat(table):
    seats = table.get("seats")
    self_num = table.get("selfSeatNumber")
    if isinstance(seats, list):
        if self_num is not None:
            for s in seats:
                if isinstance(s, dict) and s.get("seatNumber") == self_num:
                    return s
        for s in seats:
            if isinstance(s, dict) and s.get("holeCards"):
                return s
    return {}


def _capability_names(allowed):
    names = set()
    if not isinstance(allowed, dict):
        return names
    avail = allowed.get("availableActions")
    if isinstance(avail, list):
        for a in avail:
            if isinstance(a, str):
                names.add(a.lower())
            elif isinstance(a, dict):
                n = a.get("action") or a.get("name") or a.get("type")
                if n:
                    names.add(str(n).lower())
    bool_map = {
        "canFold": "fold", "canCheck": "check", "canCall": "call",
        "canBet": "bet", "canRaise": "raise", "canAllIn": "allin",
    }
    for flag_key, action_name in bool_map.items():
        if allowed.get(flag_key):
            names.add(action_name)
    return names


def _clamp_to_amount(target, lo, hi):
    try:
        if lo is not None:
            target = max(target, int(lo))
    except (TypeError, ValueError):
        pass
    try:
        if hi is not None:
            hi_i = int(hi)
            if lo is None or hi_i >= (int(lo) if isinstance(lo, (int, float)) else hi_i):
                target = min(target, hi_i)
            else:
                target = hi_i
    except (TypeError, ValueError):
        pass
    return max(target, 1)


def _safe_fallback(allowed_names, to_call, call_amount):
    order = ["fold", "call", "check"] if to_call > 0 else ["check", "call", "fold"]
    for name in order:
        if not allowed_names or name in allowed_names:
            amount = call_amount if name == "call" else 0
            return {"action": name, "amount": int(amount),
                    "message": "playing it safe", "reasoning": "fallback safe action"}
    if allowed_names:
        name = next(iter(allowed_names))
        return {"action": name, "amount": 0, "message": "playing it safe", "reasoning": "fallback safe action"}
    return {"action": "fold" if to_call > 0 else "check", "amount": 0,
            "message": "playing it safe", "reasoning": "fallback safe action"}


def decide(state):
    allowed_names = set()
    to_call = 0
    call_amount = 0
    try:
        table = _pick_table(state)
        allowed = table.get("allowedActions")
        allowed_names = _capability_names(allowed)
        if not isinstance(allowed, dict):
            allowed = {}

        self_seat = _find_self_seat(table)

        hole_raw = self_seat.get("holeCards") or table.get("holeCards") or []
        board_raw = table.get("boardCards") or table.get("board") or []
        pot = table.get("potChips", table.get("pot", 0)) or 0
        current_bet = table.get("currentBet")
        my_bet = self_seat.get("currentBetChips", 0) or 0
        stack = self_seat.get("stackChips")
        if stack is None:
            stack = self_seat.get("totalChips")

        call_to_amount = allowed.get("callToAmount")
        call_chips = allowed.get("callChips")
        if call_chips is None:
            call_chips = allowed.get("callAmount")
        if call_chips is None and call_to_amount is not None:
            try:
                call_chips = max(0, int(call_to_amount) - int(my_bet))
            except (TypeError, ValueError):
                call_chips = 0
        if call_chips is None and current_bet is not None:
            try:
                call_chips = max(0, int(current_bet) - int(my_bet))
            except (TypeError, ValueError):
                call_chips = 0
        to_call = call_chips or 0
        if call_to_amount is not None:
            call_amount = call_to_amount
        elif current_bet is not None:
            call_amount = current_bet
        else:
            call_amount = my_bet + to_call

        min_bet_to = allowed.get("minBet")
        min_raise_to = allowed.get("minRaiseTo", table.get("minRaiseTo"))
        max_commit = allowed.get("maxCommit")
        allin_to = allowed.get("allInToAmount")
        bet_range = allowed.get("betRange") or []
        raise_range = allowed.get("raiseRange") or []

        num_opponents = 1
        seats = table.get("seats")
        if isinstance(seats, list):
            active = sum(1 for s in seats if isinstance(s, dict)
                         and s.get("status") in ("Active", "AllIn") and s is not self_seat)
            if active > 0:
                num_opponents = active

        try:
            hole = [_parse_card(c) for c in hole_raw if c]
            board = [_parse_card(c) for c in board_raw if c]
        except Exception:
            hole, board = [], []

        try:
            if len(hole) == 2:
                trials = 250 if len(board) >= 3 else 150
                equity = estimate_equity(hole, board, num_opponents=num_opponents, trials=trials)
            else:
                equity = 0.5
        except Exception:
            equity = 0.5

        def has(name):
            return not allowed_names or name in allowed_names

        def sized_bet(fraction):
            lo = min_bet_to if min_bet_to is not None else (bet_range[0] if len(bet_range) > 0 else None)
            hi = max_commit if max_commit is not None else (bet_range[1] if len(bet_range) > 1 else stack)
            target = int(my_bet + max(pot, 10) * fraction)
            return _clamp_to_amount(target, lo, hi)

        def sized_raise(fraction):
            lo = min_raise_to if min_raise_to is not None else (raise_range[0] if len(raise_range) > 0 else None)
            hi = max_commit if max_commit is not None else (raise_range[1] if len(raise_range) > 1 else stack)
            base = call_amount if call_amount else (my_bet + to_call)
            target = int(base + max(pot, 10) * fraction)
            return _clamp_to_amount(target, lo, hi)

        pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0.0

        def below_true_min(amount, lo):
            try:
                return lo is not None and int(amount) < int(lo)
            except (TypeError, ValueError):
                return False

        if to_call <= 0:
            if equity >= 0.68 and has("bet"):
                amount = sized_bet(0.75 if equity > 0.82 else 0.5)
                if below_true_min(amount, min_bet_to) and has("allin"):
                    action, amount = "allin", int(allin_to if allin_to is not None else amount)
                else:
                    action = "bet"
                msg = "strong equity here, betting for value"
            elif equity >= 0.68 and has("raise"):
                amount = sized_raise(0.75 if equity > 0.82 else 0.5)
                if below_true_min(amount, min_raise_to) and has("allin"):
                    action, amount = "allin", int(allin_to if allin_to is not None else amount)
                else:
                    action = "raise"
                msg = "strong equity here, betting for value"
            elif has("check"):
                action, amount, msg = "check", 0, "not enough here to bet, taking a free card"
            elif has("call"):
                action, amount, msg = "call", call_amount, "checking isn't available, taking the passive line"
            elif has("fold"):
                action, amount, msg = "fold", 0, "no good option to see another card cheaply"
            else:
                action, amount, msg = next(iter(allowed_names), "check"), 0, "only line available"
        else:
            if equity >= 0.72 and has("raise"):
                amount = sized_raise(0.75)
                if below_true_min(amount, min_raise_to) and has("allin"):
                    action, amount = "allin", int(allin_to if allin_to is not None else amount)
                else:
                    action = "raise"
                msg = "way ahead here, raising for value"
            elif equity >= pot_odds + 0.06 and has("call"):
                action, amount, msg = "call", call_amount, "price is right for this equity"
            elif has("fold"):
                action, amount, msg = "fold", 0, "not enough equity to pay this price"
            elif has("check"):
                action, amount, msg = "check", 0, "taking the free option since folding isn't better"
            elif has("call"):
                action, amount, msg = "call", call_amount, "no fold available, taking the call"
            else:
                action, amount, msg = next(iter(allowed_names), "fold"), call_amount, "only line available"

        return {"action": action, "amount": int(amount), "message": msg, "reasoning": msg}
    except Exception:
        return _safe_fallback(allowed_names, to_call, call_amount)


def act(state):
    return decide(state)


def get_action(state):
    return decide(state)


def on_decision(state):
    return decide(state)


def strategy(state):
    return decide(state)
