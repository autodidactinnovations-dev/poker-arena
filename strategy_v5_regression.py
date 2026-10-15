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

v5: adds three refinements on top of v4's Monte Carlo equity engine, measured
against Eval S1's +72.7 bb/100 v4 baseline:
  - Position awareness: `_compute_position` derives button/blind seats from
    BlindPosted events in `recentEvents` and computes a 0..1 "how late am I"
    fraction, used to widen preflop ranges and lean slightly more aggressive
    postflop in position (and more cautious out of position).
  - Better preflop ranges: preflop strength blends Monte Carlo equity with a
    simplified Chen-formula tier (`_chen_score`), since raw equity-vs-random
    alone doesn't capture standard range-based preflop play. Distinguishes
    unopened pots, cheap-to-continue spots, and facing-a-raise spots with
    different thresholds instead of one flat equity cutoff.
  - Opponent-tendency-aware bet sizing: `_update_opponent_model` accumulates
    rough VPIP/aggression stats per opponent agentId from recentEvents across
    the match (module-level state; degrades gracefully to neutral defaults
    if the runtime doesn't persist state across hands). Value bets/raises are
    sized larger against looser/more passive opponents, and postflop calling
    requires less edge when opponents are looser.

Decision logic still falls back to a legal-flag-aware safe action (fold when
facing a bet, check otherwise) on any parsing/computation failure, instead of
a hardcoded action that might be illegal.
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


def _chen_score(hole):
    """Simplified Chen-formula preflop hand strength, roughly 0..20 (AA highest)."""
    try:
        (v1, s1), (v2, s2) = hole
    except Exception:
        return 6.0
    high, low = max(v1, v2), min(v1, v2)
    base_map = {14: 10.0, 13: 8.0, 12: 7.0, 11: 6.0}
    base = base_map.get(high, high / 2.0)
    if v1 == v2:
        return max(base * 2.0, 5.0)
    score = base
    if s1 == s2:
        score += 2.0
    gap = high - low - 1
    if gap == 1:
        score -= 1.0
    elif gap == 2:
        score -= 2.0
    elif gap == 3:
        score -= 4.0
    elif gap >= 4:
        score -= 5.0
    if gap <= 1 and low >= 10:
        score += 1.0
    return max(score, 0.0)


def _compute_position(table, self_seat):
    """Position within the hand: frac 0 (first to act postflop, worst) to 1 (button, best)."""
    try:
        seats = table.get("seats") or []
        seat_nums = sorted(s.get("seatNumber") for s in seats
                            if isinstance(s, dict) and s.get("seatNumber") is not None)
        n = len(seat_nums)
        my_num = self_seat.get("seatNumber")
        if n < 2 or my_num is None or my_num not in seat_nums:
            return {"frac": 0.5, "n": n, "is_blind": False}

        sb_chips = table.get("smallBlindChips")
        bb_chips = table.get("bigBlindChips")
        sb_seat = bb_seat = None
        for ev in (table.get("recentEvents") or []):
            if ev.get("type") != "BlindPosted":
                continue
            summ = ev.get("summary") or {}
            amt, sn = summ.get("amount"), summ.get("seatNumber")
            if amt == sb_chips and sb_seat is None:
                sb_seat = sn
            elif amt == bb_chips and bb_seat is None:
                bb_seat = sn
        if sb_seat is None or sb_seat not in seat_nums:
            return {"frac": 0.5, "n": n, "is_blind": False}

        if n == 2:
            btn_seat = sb_seat
        else:
            idx_sb = seat_nums.index(sb_seat)
            btn_seat = seat_nums[idx_sb - 1]

        idx_btn = seat_nums.index(btn_seat)
        order = seat_nums[idx_btn + 1:] + seat_nums[:idx_btn + 1]
        my_idx = order.index(my_num)
        frac = my_idx / max(1, n - 1)
        is_blind = my_num in (sb_seat, bb_seat)
        return {"frac": frac, "n": n, "is_blind": is_blind}
    except Exception:
        return {"frac": 0.5, "n": 0, "is_blind": False}


_OPPONENT_STATS = {}
_SEEN_EVENT_IDS = set()


def _update_opponent_model(table):
    """Accumulate rough VPIP/aggression stats per opponent agentId from recentEvents.
    Safe to call repeatedly with overlapping event lists (dedup by event id); if the
    runtime doesn't persist this module across the whole match, stats just stay empty
    and callers fall back to neutral defaults."""
    try:
        seats = table.get("seats") or []
        seat_to_agent = {s.get("seatNumber"): s.get("agentId") for s in seats if isinstance(s, dict)}
        for ev in (table.get("recentEvents") or []):
            eid = ev.get("id")
            if not eid or eid in _SEEN_EVENT_IDS:
                continue
            _SEEN_EVENT_IDS.add(eid)
            if ev.get("type") not in ("ActionTaken", "TimeoutAction"):
                continue
            summary = ev.get("summary") or {}
            action, seat_num, street = summary.get("action"), summary.get("seatNumber"), ev.get("street")
            if not action or seat_num is None:
                continue
            agent_id = seat_to_agent.get(seat_num)
            if not agent_id:
                continue
            stats = _OPPONENT_STATS.setdefault(agent_id, {"vpip": 0, "agg": 0, "passive": 0})
            if street == "Preflop" and action in ("call", "bet", "raise", "all-in"):
                stats["vpip"] += 1
            elif street in ("Flop", "Turn", "River"):
                if action in ("bet", "raise", "all-in"):
                    stats["agg"] += 1
                elif action in ("call", "check"):
                    stats["passive"] += 1
        if len(_SEEN_EVENT_IDS) > 20000:
            _SEEN_EVENT_IDS.clear()
    except Exception:
        pass


def _opp_profile(agent_ids):
    """Return (looseness, aggression) in [0,1] averaged over given opponent ids.
    Neutral (0.4, 0.35) defaults for unseen opponents."""
    looseness_vals, agg_vals = [], []
    for aid in agent_ids:
        s = _OPPONENT_STATS.get(aid)
        if not s:
            continue
        total = s["vpip"]
        looseness_vals.append(min(1.0, total / 6.0))
        acted = s["agg"] + s["passive"]
        if acted > 0:
            agg_vals.append(s["agg"] / acted)
    looseness = sum(looseness_vals) / len(looseness_vals) if looseness_vals else 0.4
    aggression = sum(agg_vals) / len(agg_vals) if agg_vals else 0.35
    return looseness, aggression


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
        opponent_ids = []
        if isinstance(seats, list):
            active_seats = [s for s in seats if isinstance(s, dict)
                             and s.get("status") in ("Active", "AllIn") and s is not self_seat]
            if active_seats:
                num_opponents = len(active_seats)
            opponent_ids = [s.get("agentId") for s in active_seats if s.get("agentId")]

        _update_opponent_model(table)
        looseness, aggression = _opp_profile(opponent_ids)
        position = _compute_position(table, self_seat)
        pos_bonus = (position["frac"] - 0.5) * 0.20
        loose_bonus = (looseness - 0.4) * 0.10

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

        chen_norm = min(1.0, _chen_score(hole) / 20.0) if len(hole) == 2 else 0.5

        def has(name):
            return not allowed_names or name in allowed_names

        def sized_bet(fraction):
            frac = max(0.3, min(1.1, fraction * (1.0 + 0.25 * (looseness - 0.4))))
            lo = min_bet_to if min_bet_to is not None else (bet_range[0] if len(bet_range) > 0 else None)
            hi = max_commit if max_commit is not None else (bet_range[1] if len(bet_range) > 1 else stack)
            target = int(my_bet + max(pot, 10) * frac)
            return _clamp_to_amount(target, lo, hi)

        def sized_raise(fraction):
            frac = max(0.3, min(1.1, fraction * (1.0 + 0.25 * (looseness - 0.4))))
            lo = min_raise_to if min_raise_to is not None else (raise_range[0] if len(raise_range) > 0 else None)
            hi = max_commit if max_commit is not None else (raise_range[1] if len(raise_range) > 1 else stack)
            base = call_amount if call_amount else (my_bet + to_call)
            target = int(base + max(pot, 10) * frac)
            return _clamp_to_amount(target, lo, hi)

        pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0.0

        def below_true_min(amount, lo):
            try:
                return lo is not None and int(amount) < int(lo)
            except (TypeError, ValueError):
                return False

        street = table.get("street")
        bb_chips = table.get("bigBlindChips")

        if street == "Preflop":
            # Blend raw hand strength (Monte Carlo vs random hands) with a Chen-formula
            # tier, then adjust for position and observed opponent looseness so ranges
            # widen in late position / vs loose tables and tighten early / vs nits.
            strength = 0.55 * equity + 0.45 * chen_norm
            adj_strength = strength + pos_bonus + loose_bonus
            facing_raise = bb_chips is not None and (current_bet or 0) > bb_chips

            if to_call <= 0:
                if adj_strength >= 0.60 and has("raise"):
                    amount = sized_raise(0.65 + 0.15 * max(0, looseness - 0.4))
                    if below_true_min(amount, min_raise_to) and has("allin"):
                        action, amount = "allin", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "opening for value from here"
                elif has("check"):
                    action, amount, msg = "check", 0, "checking my option"
                elif has("call"):
                    action, amount, msg = "call", call_amount, "taking the passive line"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "no good option here"
                else:
                    action, amount, msg = next(iter(allowed_names), "check"), 0, "only line available"
            elif facing_raise:
                if adj_strength >= 0.74 and has("raise"):
                    amount = sized_raise(0.85)
                    if below_true_min(amount, min_raise_to) and has("allin"):
                        action, amount = "allin", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "premium hand, re-raising for value"
                elif adj_strength >= 0.56 and equity >= pot_odds - 0.02 and has("call"):
                    action, amount, msg = "call", call_amount, "strong enough to continue"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "not strong enough to defend this raise"
                elif has("check"):
                    action, amount, msg = "check", 0, "taking the free option"
                elif has("call"):
                    action, amount, msg = "call", call_amount, "no fold available, taking the call"
                else:
                    action, amount, msg = next(iter(allowed_names), "fold"), call_amount, "only line available"
            else:
                # Cheap price to continue (just the blind, no raise yet)
                if adj_strength >= 0.60 and has("raise"):
                    amount = sized_raise(0.7)
                    if below_true_min(amount, min_raise_to) and has("allin"):
                        action, amount = "allin", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "isolating for value"
                elif adj_strength >= 0.38 and has("call"):
                    action, amount, msg = "call", call_amount, "cheap price, defending"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "not worth even the cheap price"
                elif has("check"):
                    action, amount, msg = "check", 0, "taking the free option"
                else:
                    action, amount, msg = next(iter(allowed_names), "fold"), call_amount, "only line available"
        else:
            # Postflop: equity-driven as before, nudged by position and sized by
            # observed opponent looseness (handled inside sized_bet/sized_raise).
            value_thresh = 0.68 - pos_bonus * 0.3
            raise_thresh = 0.72 - pos_bonus * 0.3
            if to_call <= 0:
                if equity >= value_thresh and has("bet"):
                    amount = sized_bet(0.75 if equity > 0.82 else 0.5)
                    if below_true_min(amount, min_bet_to) and has("allin"):
                        action, amount = "allin", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "bet"
                    msg = "strong equity here, betting for value"
                elif equity >= value_thresh and has("raise"):
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
                call_margin = 0.06 - loose_bonus * 0.5
                if equity >= raise_thresh and has("raise"):
                    amount = sized_raise(0.75)
                    if below_true_min(amount, min_raise_to) and has("allin"):
                        action, amount = "allin", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "way ahead here, raising for value"
                elif equity >= pot_odds + call_margin and has("call"):
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
