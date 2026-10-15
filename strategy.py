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

v5 (submitted, regressed to -0.1 raw / +7.47 adjusted bb/100 vs v4's +72.7):
tried position awareness + opponent-tendency bet sizing + "better preflop
ranges" via a Chen-formula tier blended 45/55 with Monte Carlo equity. The
Chen blend tightened preflop far too much and tanked results. Kept as
strategy_v5_regression.py for reference; do not reuse its preflop blend.

v6: keeps v5's position awareness (`_compute_position`) and opponent-tendency
tracking (`_update_opponent_model`/`_opp_profile`) for bet sizing, but rebuilds
the preflop decision around v4's proven equity-vs-pot-odds shape (wide calling,
selective raising) instead of the Chen tier. Public agent-stats for Eval S1's
top leaderboard entries (fetched via /api/arena/texas/agent-stats) show the
best performers against this fixed reference panel play very LOOSE (80-93%
VPIP) rather than tight/disciplined ranges, consistent with v4 > v5. Chen score
now only nudges raise/open thresholds slightly (never gates calling); position
and observed opponent looseness widen raise/call thresholds in position and vs
loose opponents, narrowing them early and vs tight opponents.

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


def estimate_equity(hole, board, num_opponents=1, trials=200, pending_opponents=0, pending_weight=0.35,
                     range_strength=0.0, range_retries=3):
    """Monte Carlo win probability vs `num_opponents` opponents who are certainly
    live (already voluntarily acted this street) plus `pending_opponents` who
    have not acted yet and are included independently with probability
    `pending_weight` per trial (most not-yet-acted players fold before the
    action reaches showdown, so treating them as certain full-strength random
    hands overstates the field size - see live_agent.py's decide_live for the
    caller that separates certain vs pending seats). Defaults preserve the
    original all-certain behavior for existing callers.

    `range_strength` (0..1, default 0 = old uniform-random behavior) softly
    biases sampled opponent hole cards away from complete air (no pair, no
    better) toward the board: on each trial, if every included opponent's
    best hand is category 0 (`_hand_rank_5`'s no-pair bucket), the whole
    trial's draw is rejected and redrawn with probability `range_strength`,
    up to `range_retries` times. This targets FINDINGS.md's Leak 6 - a
    uniform-random opponent hand overstates our equity most exactly when an
    opponent has proven, by continuing to put money in on prior streets, that
    they are NOT holding a random hand. Confirmed live in table cmtb7bys
    (2026-08-27): Q8 top pair barreled flop/turn/river at 76%/87%/84%
    modeled equity into an opponent who had called both prior streets and
    turned over a better kicker for -83 chips. See `cumulative_aggression` /
    `aggression_to_range_strength` for how callers set this parameter - 0
    before an opponent has done anything, rising monotonically as they bet and
    raise across the hand. The "at least a pair" bar is a coarse first cut, not a
    real range model - it removes the ~50% of random hands that a genuine
    calling range would never contain, but doesn't yet distinguish top pair
    from two pair or better within that range."""
    if len(hole) != 2:
        return 0.5

    known = set(hole) | set(board)
    deck = [_parse_card(x) for x in FULL_DECK]
    deck = [c for c in deck if c not in known]
    need_board = 5 - len(board)
    total_slots = num_opponents + pending_opponents
    draw_count = need_board + 2 * total_slots

    if draw_count > len(deck) or trials <= 0:
        return 0.5

    wins = 0.0
    for _ in range(trials):
        for attempt in range(range_retries + 1):
            drawn = random.sample(deck, draw_count)
            extra_board = drawn[:need_board]
            opp_cards = drawn[need_board:]
            full_board = board + extra_board

            my_rank = _best_hand_rank(hole + full_board)
            opp_ranks = []
            for i in range(num_opponents):
                opp_hole = opp_cards[2 * i:2 * i + 2]
                opp_ranks.append(_best_hand_rank(opp_hole + full_board))
            for j in range(pending_opponents):
                if random.random() < pending_weight:
                    idx = num_opponents + j
                    opp_hole = opp_cards[2 * idx:2 * idx + 2]
                    opp_ranks.append(_best_hand_rank(opp_hole + full_board))

            if range_strength <= 0 or not opp_ranks or attempt == range_retries:
                break
            if any(r[0] > 0 for r in opp_ranks) or random.random() >= range_strength:
                break
            # every included opponent whiffed completely (no pair) - reject and redraw

        if not opp_ranks:
            wins += 1.0
            continue

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


def _acted_seat_numbers(table):
    """Seat numbers that have taken a voluntary action (call/check/bet/raise/
    all-in) on the CURRENT street, per recentEvents. Blinds posted don't count
    - a seat that has only posted a forced blind hasn't chosen to contest the
    pot yet and belongs in the pending, not certain, opponent bucket."""
    street = table.get("street")
    acted = set()
    for ev in (table.get("recentEvents") or []):
        if ev.get("street") != street or ev.get("type") not in ("ActionTaken", "TimeoutAction"):
            continue
        summary = ev.get("summary") or {}
        action, seat_num = summary.get("action"), summary.get("seatNumber")
        if seat_num is not None and action in ("call", "check", "bet", "raise", "all-in"):
            acted.add(seat_num)
    return acted


def _opponent_raises_this_street(table, self_seat_num):
    """Count of raise/all-in actions taken by seats OTHER than us on the
    CURRENT street, per recentEvents. A raise carries real information no
    equity-vs-random-hand simulation can see - the round-51 bust (busted the
    whole 1916-chip stack in a three-raise turn war at a self-computed 72-77%
    equity the whole way, with zero range modeling to catch that a repeated
    re-raise almost always means we're beat) is exactly the failure mode
    this is meant to catch: see the escalation_bump usage in decide_live."""
    street = table.get("street")
    count = 0
    for ev in (table.get("recentEvents") or []):
        if ev.get("street") != street or ev.get("type") not in ("ActionTaken", "TimeoutAction"):
            continue
        summary = ev.get("summary") or {}
        action, seat_num = summary.get("action"), summary.get("seatNumber")
        if seat_num is not None and seat_num != self_seat_num and action in ("raise", "all-in"):
            count += 1
    return count


def _limpers_this_street(table, self_seat_num):
    """Count of seats OTHER than us that have CALLED on the current street.

    Preflop with no raise yet, this is the limper count, which is what an
    iso-raise has to be sized against: each extra player already in adds a
    big blind of dead money and one more hand to get through, so 4bb is the
    right price heads-up and much too cheap five-handed. Reads the same
    recentEvents stream as _opponent_raises_this_street so the two agree
    about what happened this street."""
    street = table.get("street")
    count = 0
    for ev in (table.get("recentEvents") or []):
        if ev.get("street") != street or ev.get("type") not in ("ActionTaken", "TimeoutAction"):
            continue
        summary = ev.get("summary") or {}
        action, seat_num = summary.get("action"), summary.get("seatNumber")
        if seat_num is not None and seat_num != self_seat_num and action == "call":
            count += 1
    return count

# --- Cumulative aggression model -------------------------------------------
# Replaces the per-street snapshot that fed range_strength. That snapshot had
# two defects, both observed live:
#
#   1. It counted only RAISES. A villain firing a continuation bet registered
#      as zero aggression, so every flop call in the S16 fold cluster
#      (cmteft6d, cmtec2xv, cmtef5h0, cmtef06p, cmtefdco) was priced at full
#      confidence against an opponent who was actively betting at us.
#   2. It RESET at each street boundary. On S17 hand cmtewk95 the discount went
#      0.70 on the flop (villain raised twice) down to 0.35 on the turn, because
#      `opp_raises_this_street` had reset and `prior_streets` only counted HOW
#      MANY streets the villain continued, not how hard. The turn bet of 204
#      chips therefore went out at an inflated 0.776 equity, and that hand lost
#      421 chips.
#
# Aggression is evidence, and evidence accumulates. This walks the whole hand
# and never decrements.
#
# Weighting rationale:
#   Street - ranges narrow as the board completes, so the same action means
#   more later. A preflop raise is cheap and wide; a river raise is not.
#   Action - a raise faces existing money and escalates, so it is stronger
#   evidence than a bet, which is stronger than a call. Calls are included at
#   low weight because passive continuation is still real information (it is
#   what the original continuation-count signal measured), just weak.
#
# Checks and folds add nothing. They do NOT decrement: a villain who bets the
# flop and checks the turn has still bet the flop. Decay would reintroduce the
# reset bug in softer form. Modelling checks as genuine negative evidence is a
# possible refinement, deliberately not attempted here.
_AGG_STREET_WEIGHT = {"Preflop": 0.5, "Flop": 1.0, "Turn": 1.5, "River": 2.0}
_AGG_ACTION_WEIGHT = {"call": 0.5, "bet": 1.0, "raise": 2.0, "all-in": 2.5}


def cumulative_aggression(table, self_seat_num):
    """Total weighted aggression shown by ALL opponents so far this hand.

    Monotonic non-decreasing as a hand progresses. Stateless - recomputed from
    `recentEvents` on every poll, so it needs no cross-process persistence.

    Caveat: if the API ever truncates `recentEvents` mid-hand, early actions
    fall out of the window and this under-counts. It cannot over-count."""
    score = 0.0
    for ev in (table.get("recentEvents") or []):
        if ev.get("type") not in ("ActionTaken", "TimeoutAction"):
            continue
        summary = ev.get("summary") or {}
        action, seat_num = summary.get("action"), summary.get("seatNumber")
        if seat_num is None or seat_num == self_seat_num:
            continue
        aw = _AGG_ACTION_WEIGHT.get(action)
        if not aw:
            continue
        score += aw * _AGG_STREET_WEIGHT.get(ev.get("street"), 1.0)
    return score


# Saturating map from raw score to a range_strength discount. Chosen over a
# linear clamp so the signal degrades gracefully and cannot overshoot: one
# flop bet (~1.0) gives a mild ~0.20, a bet-plus-raise (~3.0) gives ~0.44, and
# sustained multi-street aggression (~8.0) approaches the 0.70 ceiling.
AGGRESSION_RS_MAX = 0.70
AGGRESSION_RS_SCALE = 3.0


def aggression_to_range_strength(score):
    import math
    if score <= 0:
        return 0.0
    return AGGRESSION_RS_MAX * (1.0 - math.exp(-score / AGGRESSION_RS_SCALE))


def _last_aggressor_seat(table, self_seat_num):
    """Seat number of whoever most recently bet/raised/all-in'd on the
    CURRENT street (excluding us), or None if nobody has. This is who we're
    actually responding to when to_call > 0 - used to look up that specific
    opponent's real long-run stats (/texas/agent-stats) rather than treating
    every bet as equally informative regardless of who made it."""
    street = table.get("street")
    last_seat = None
    for ev in (table.get("recentEvents") or []):
        if ev.get("street") != street or ev.get("type") not in ("ActionTaken", "TimeoutAction"):
            continue
        summary = ev.get("summary") or {}
        action, seat_num = summary.get("action"), summary.get("seatNumber")
        if seat_num is not None and seat_num != self_seat_num and action in ("bet", "raise", "all-in"):
            last_seat = seat_num
    return last_seat


def opponent_read_bump(stats):
    """Turn one opponent's real /texas/agent-stats into a threshold bump:
    positive = be MORE cautious continuing against this specific opponent's
    aggression (their bets tend to mean something), negative = be MORE
    willing to look them up (they bet/raise with a lot of air). Returns 0.0
    for None/insufficient-sample stats (`playingStyle` is null below the
    endpoint's own sample-size floor) - fall back to the generic thresholds
    rather than guess from a small sample.

    This replaces relying on the in-process VPIP/AF tracker
    (`_update_opponent_model`/`_opp_profile`) for this specific purpose - that
    tracker resets every ~7-minute live_agent.py process and rarely
    accumulates enough hands to say anything, whereas agent-stats is a
    stable, cross-session read computed server-side."""
    if not stats:
        return 0.0
    style = stats.get("playingStyle")
    if not style:
        return 0.0
    bump = 0.0
    tightness = style.get("tightness")
    if tightness == "tight":
        bump += 0.08
    elif tightness == "loose":
        bump -= 0.05
    bluff = stats.get("bluffPct")
    if bluff is not None:
        bump += (0.25 - bluff) * 0.3
    return max(-0.15, min(0.20, bump))


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
            # v6: v4's proven equity-vs-pot-odds shape (wide calling, selective
            # raising) is the primary driver — a v5 attempt that leaned hard on a
            # Chen-formula tier (45% of the decision weight) tightened preflop far
            # too much and cratered results (+72.7 -> -0.1 bb/100) against this
            # fixed reference panel, which public agent-stats show is beaten by
            # LOOSE volume play, not tight discipline. Chen now only nudges the
            # raise/open thresholds slightly (not the calling decision at all);
            # position and observed opponent looseness do the real range-shaping,
            # matching standard theory (raise/call wider in position, wider vs
            # loose opponents) without abandoning the equity signal that worked.
            strength = equity + 0.08 * (chen_norm - 0.5)
            raise_pos_adj = pos_bonus * 0.5 + loose_bonus * 0.4
            call_margin = max(0.0, 0.06 - loose_bonus - pos_bonus * 0.3)
            facing_raise = bb_chips is not None and (current_bet or 0) > bb_chips

            if to_call <= 0:
                open_thresh = 0.68 - raise_pos_adj
                if strength >= open_thresh and has("raise"):
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
                raise_thresh = 0.72 - raise_pos_adj
                if strength >= raise_thresh and has("raise"):
                    amount = sized_raise(0.85)
                    if below_true_min(amount, min_raise_to) and has("allin"):
                        action, amount = "allin", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "premium hand, re-raising for value"
                elif equity >= pot_odds + call_margin and has("call"):
                    action, amount, msg = "call", call_amount, "price is right for this equity"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "not enough equity to pay this price"
                elif has("check"):
                    action, amount, msg = "check", 0, "taking the free option"
                elif has("call"):
                    action, amount, msg = "call", call_amount, "no fold available, taking the call"
                else:
                    action, amount, msg = next(iter(allowed_names), "fold"), call_amount, "only line available"
            else:
                # Cheap price to continue (just the blind, no raise yet)
                isolate_thresh = 0.66 - raise_pos_adj
                if strength >= isolate_thresh and has("raise"):
                    amount = sized_raise(0.7)
                    if below_true_min(amount, min_raise_to) and has("allin"):
                        action, amount = "allin", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "isolating for value"
                elif equity >= pot_odds + call_margin and has("call"):
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


# --- §22 action-conditioned ranges (SHADOW ONLY) ----------------------------
# P(opponent class | street, action), measured from hand_archive.jsonl by
# analysis/fit_action_ranges.py on 2026-10-08 (7,786 hands, all seats' hole
# cards visible). Classes: 0 = air (no pair), 1 = one pair, 2 = two pair+,
# classified on the board AS IT STOOD at that action. Rows missing here
# (turn raise/call, all river) are treated as uninformative (ratio 1.0).
# Re-fit after any season change (Principle 11). Nothing reads
# estimate_equity_ranged's output except the §22 shadow logger (§13).
_S22_CLASS_GIVEN_ACTION = {
    ("Flop", "check"): (0.64, 0.30, 0.05),
    ("Flop", "call"):  (0.21, 0.68, 0.11),
    ("Flop", "bet"):   (0.25, 0.61, 0.13),
    ("Flop", "raise"): (0.00, 0.38, 0.62),   # n=34 - thin
    ("Turn", "check"): (0.41, 0.44, 0.15),
    ("Turn", "bet"):   (0.17, 0.53, 0.31),
}
_S22_BASE = {"Flop": (0.41, 0.48, 0.10), "Turn": (0.29, 0.48, 0.23)}
_S22_FLOOR = 0.03      # no class is ever ruled out entirely on thin data
_S22_BOARD_LEN = {"Flop": 3, "Turn": 4, "River": 5}


def _s22_ratio(street, action):
    if action == "all-in":
        action = "raise"
    row = _S22_CLASS_GIVEN_ACTION.get((street, action))
    base = _S22_BASE.get(street)
    if not row or not base:
        return None
    return tuple(max(p, _S22_FLOOR) / b for p, b in zip(row, base))


def _s22_class(cards):
    cat = _best_hand_rank(cards)[0]
    return 0 if cat == 0 else 1 if cat == 1 else 2


def opponent_postflop_actions(table, self_seat_num):
    """{seat: [(street, action), ...]} for every postflop action by others
    this hand, in order, from recentEvents."""
    out = {}
    for ev in (table.get("recentEvents") or []):
        st = ev.get("street")
        if st not in _S22_BOARD_LEN or ev.get("type") not in ("ActionTaken", "TimeoutAction"):
            continue
        sm = ev.get("summary") or {}
        seat, action = sm.get("seatNumber"), sm.get("action")
        if seat is None or seat == self_seat_num or not action:
            continue
        out.setdefault(seat, []).append((st, action))
    return out


def estimate_equity_ranged(hole, board, opp_action_lists, pending_opponents=0,
                           pending_weight=0.35, trials=600, max_tries=40):
    """Monte Carlo equity where each CERTAIN opponent's hand is sampled from a
    Loki-style weight table: acceptance probability is the product, over every
    postflop action they took, of P(class|action)/P(class), normalised by the
    best achievable product. Pending opponents stay uniform random, exactly as
    in estimate_equity. Returns (equity, accept_rate)."""
    if len(hole) != 2:
        return 0.5, None
    known = set(hole) | set(board)
    deck = [c for c in (_parse_card(x) for x in FULL_DECK) if c not in known]
    need_board = 5 - len(board)
    # Pre-compute per-opponent (ratios, board_len) lists and normaliser.
    opp_specs = []
    for acts in opp_action_lists:
        spec = []
        for st, a in acts:
            r = _s22_ratio(st, a)
            if r and len(board) >= _S22_BOARD_LEN[st]:
                spec.append((r, _S22_BOARD_LEN[st]))
        best = 1.0
        if spec:
            best = max(_prod(r[k] for r, _ in spec) for k in range(3))
        opp_specs.append((spec, best))
    n_cert = len(opp_specs)
    if need_board + 2 * (n_cert + pending_opponents) > len(deck) or trials <= 0:
        return 0.5, None

    wins = 0.0
    accepted = attempts = 0
    for _ in range(trials):
        avail = list(deck)
        random.shuffle(avail)
        ptr = 0
        opp_holes = []
        for spec, best in opp_specs:
            chosen = None
            for _t in range(max_tries):
                if ptr + 2 > len(avail):
                    break
                cand = avail[ptr:ptr + 2]
                attempts += 1
                w = 1.0
                for r, blen in spec:
                    w *= r[_s22_class(cand + board[:blen])]
                if random.random() < w / best:
                    chosen = cand
                    accepted += 1
                    ptr += 2
                    break
                # rejected: rotate these two cards to the back so they stay available
                avail.append(avail.pop(ptr)); avail.append(avail.pop(ptr))
            if chosen is None:
                chosen = avail[ptr:ptr + 2]
                ptr += 2
            opp_holes.append(chosen)
        rest = avail[ptr:]
        extra = rest[:need_board]
        rest = rest[need_board:]
        full_board = board + extra
        my_rank = _best_hand_rank(hole + full_board)
        ranks = [_best_hand_rank(h + full_board) for h in opp_holes]
        for j in range(pending_opponents):
            if random.random() < pending_weight and len(rest) >= 2:
                ranks.append(_best_hand_rank(rest[:2] + full_board))
            rest = rest[2:]
        if not ranks:
            wins += 1.0
            continue
        top = max(ranks)
        if my_rank > top:
            wins += 1.0
        elif my_rank == top:
            wins += 1.0 / (1 + sum(1 for x in ranks if x == top))
    return wins / trials, (accepted / attempts if attempts else None)


def _prod(xs):
    p = 1.0
    for x in xs:
        p *= x
    return p


# --- §24 beat-rate lookup (SHADOW ONLY) -------------------------------------
# P(the last aggressor is AHEAD of our hand class right now | street, their
# action + size vs pot), counted directly from hand_archive.jsonl by
# analysis/fit_beat_rates.py -> analysis/beat_rates.json. On 35 held-out
# brake/cap hands: modeled equity 0.56, this 0.27, truth 0.23 (DESIGN_BACKLOG
# §24). Read only by the §24 shadow logger (§13).
_S24_TABLE = None
_S24_MIN_N = 15
_S24_RANKS = "23456789TJQKA"


def _s24_table():
    global _S24_TABLE
    if _S24_TABLE is None:
        import json, os
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis", "beat_rates.json")
        try:
            _S24_TABLE = json.load(open(p, encoding="utf-8"))
        except Exception:
            _S24_TABLE = {}
    return _S24_TABLE


def s24_hand_class(hole_raw, board_raw):
    cat = _best_hand_rank([_parse_card(c) for c in hole_raw + board_raw])[0]
    if cat == 0: return "air"
    if cat >= 3: return "trips+"
    if cat == 2: return "two_pair"
    br = sorted((_S24_RANKS.index(c[0]) for c in board_raw), reverse=True)
    hr = [_S24_RANKS.index(c[0]) for c in hole_raw]
    pr = [r for r in hr if r in br or hr.count(r) == 2]
    if not pr: return "pair_low"
    return "pair_top" if max(pr) >= br[0] else "pair_low"


def s24_last_aggression(table, self_seat_num):
    """(seat, bucket) of the last bet/raise/all-in by another seat on the
    current street, bucket in small/med/big/raise by size vs pot before it."""
    import collections
    street = table.get("street"); pot = 0; contrib = collections.Counter(); high = 0
    st = None; last = None
    for e in sorted(table.get("recentEvents") or [], key=lambda e: e.get("sequence", 0)):
        s = e.get("street"); sm = e.get("summary") or {}; seat = sm.get("seatNumber")
        if s != st and s in ("Preflop", "Flop", "Turn", "River"):
            st = s; contrib = collections.Counter(); high = 0
        if e.get("type") == "BlindPosted" and sm.get("amount"):
            contrib[seat] += sm["amount"]; pot += sm["amount"]; high = max(high, contrib[seat]); continue
        if e.get("type") not in ("ActionTaken", "TimeoutAction"): continue
        a = sm.get("action"); to = sm.get("toAmount"); add = 0
        if a in ("bet", "raise", "all-in") and to: add = max(0, to - contrib[seat])
        elif a == "call": add = sm.get("amount") or max(0, high - contrib[seat])
        else: continue
        before = pot; contrib[seat] += add; pot += add; high = max(high, contrib[seat])
        if st == street and seat != self_seat_num and a in ("bet", "raise", "all-in"):
            f = add / before if before else 1.0
            b = "raise" if a == "raise" else ("small" if f < 0.5 else "med" if f <= 1.0 else "big")
            last = (seat, b)
    return last


def s24_ahead_prob(street, bucket, hand_class):
    """Our probability of being ahead of that aggressor right now, or None."""
    t = _s24_table()
    for st, b in ((street, bucket), (street, "big" if bucket == "raise" else bucket),
                  ("Flop", bucket), ("Flop", "big" if bucket == "raise" else bucket)):
        a, n = t.get(f"{st}|{b}|{hand_class}", [0, 0])
        if n >= _S24_MIN_N:
            return 1.0 - a / n
    return None
