"""
Chipzen adapter for the poker-arena bot.

Lets the SAME decision engine that plays dev.fun (live_agent.decide_live +
strategy.py) play on chipzen.ai, without touching either file.

How it works
------------
decide_live() reads a dev.fun-shaped `table` dict. Chipzen sends its own
GameState. This module translates Chipzen -> dev.fun shape, calls
decide_live() unchanged, then translates the answer back to a Chipzen Action.

    Chipzen GameState --to_devfun_table()--> decide_live() --to_chipzen_action()--> Action

Nothing in live_agent.py or strategy.py is modified, so every fix made for
dev.fun is automatically live here too (one shared strategy, PRINCIPLES.md-
friendly: no venue-specific tuning sneaks into the core).

Things deliberately switched OFF for Chipzen
--------------------------------------------
* get_opponent_stats() -> stubbed to None. It calls dev.fun's /texas/agent-stats
  endpoint, which knows nothing about Chipzen opponents. opponent_read_bump(None)
  returns 0.0, i.e. the generic thresholds.
* S22 / S24 shadow tracks -> disabled. They're dev.fun log-only experiments
  and S22 runs an extra equity sim we don't want eating Chipzen's clock.
* live_agent.LIVE stays False -> no writes to the dev.fun shadow/archive files.

Run it (on your own machine, External-API path):
    pip install chipzen-bot==0.4.0
    # put your token in chipzen.toml:  [external_api] token = "cz_extbot_..."
    py chipzen_agent.py
"""

import live_agent as L

# --- isolate from dev.fun -------------------------------------------------
L.get_opponent_stats = lambda agent_id: None
L.S22_SHADOW_ENABLED = False
L.S24_SHADOW_ENABLED = False
L.LIVE = False

_PHASE_TO_STREET = {"preflop": "Preflop", "flop": "Flop", "turn": "Turn", "river": "River"}


def _seat_num(seat_idx):
    # dev.fun seat numbers are 1-based; Chipzen seats are 0-based.
    return int(seat_idx) + 1


def to_devfun_table(state, game_config, match_id="cz"):
    """Build a dev.fun pending-actions `table` dict from a Chipzen GameState.

    game_config: the match_start game_config (small_blind, big_blind,
    num_players). Opponent stacks are per-seat in seat order, excluding us.
    """
    phase = (state.phase or "preflop").lower()
    street = _PHASE_TO_STREET.get(phase, "Preflop")
    n = int(game_config.get("num_players") or (len(state.opponent_stacks) + 1))
    sb = int(game_config.get("small_blind") or 0)
    bb = int(game_config.get("big_blind") or 0)
    # Blinds ESCALATE during a Chipzen match (first match went 50/100 ->
    # 75/150 -> 150/300). game_config only has the starting level, so read the
    # real ones from this hand's posts. Using the stale config made the BB look
    # like a raise, so every hand was played as a "3-bet" spot.
    for a in state.action_history or []:
        if a.get("action") == "post_small_blind":
            sb = int(a.get("amount") or sb)
        elif a.get("action") == "post_big_blind":
            bb = int(a.get("amount") or bb)
    me = int(state.your_seat)

    # Stacks by seat index.
    stacks, opp = {}, list(state.opponent_stacks)
    for s in range(n):
        stacks[s] = int(state.your_stack) if s == me else (int(opp.pop(0)) if opp else 0)

    # Walk the action history: per-seat street bet, total committed, folds,
    # and dev.fun-style events.
    street_bet = {s: 0 for s in range(n)}
    total = {s: 0 for s in range(n)}
    folded = set()
    events = []
    cur_phase = "preflop"
    hid = state.round_id or f"{match_id}:h{state.hand_number}"
    for i, a in enumerate(state.action_history or []):
        seat = int(a.get("seat", 0))
        act = str(a.get("action", ""))
        amt = int(a.get("amount", 0) or 0)
        ph = str(a.get("phase", cur_phase)).lower()
        if ph != cur_phase:
            cur_phase = ph
            street_bet = {s: 0 for s in range(n)}
        ev_street = _PHASE_TO_STREET.get(ph, "Preflop")
        base = {"id": f"{hid}:{i}", "sequence": i + 1, "street": ev_street, "occurredAt": i + 1}
        if act in ("post_small_blind", "post_big_blind", "post_ante"):
            street_bet[seat] += amt
            total[seat] += amt
            if act != "post_ante":
                events.append(dict(base, type="BlindPosted", summary={
                    "action": None, "amount": amt, "toAmount": None, "seatNumber": _seat_num(seat)}))
            continue
        timeout = bool(a.get("is_timeout"))
        if act == "fold":
            folded.add(seat)
            dv_act, to_amt = "fold", None
        elif act == "check":
            dv_act, to_amt = "check", None
        elif act == "call":
            street_bet[seat] += amt
            total[seat] += amt
            dv_act, to_amt = "call", street_bet[seat]
        elif act == "raise":
            # Chipzen has only "raise"; dev.fun distinguishes an opening bet.
            facing = max(v for k, v in street_bet.items() if k != seat) if n > 1 else 0
            added = max(0, amt - street_bet[seat])
            street_bet[seat] = amt
            total[seat] += added
            dv_act = "raise" if facing > 0 else "bet"
            if stacks.get(seat, 1) == 0 and seat != me:
                dv_act = "all-in"
            to_amt = amt
        else:
            continue
        events.append(dict(base, type="TimeoutAction" if timeout else "ActionTaken", summary={
            "action": dv_act, "amount": None, "toAmount": to_amt, "seatNumber": _seat_num(seat)}))

    # Reset street bets if we're on a new street that has no actions yet.
    if cur_phase != phase:
        street_bet = {s: 0 for s in range(n)}

    seats = []
    for s in range(n):
        if s in folded:
            status = "Folded"
        elif stacks[s] == 0 and total[s] > 0:
            status = "AllIn"
        else:
            status = "Active"
        seats.append({
            "seatId": f"seat{s}", "seatNumber": _seat_num(s),
            "agentId": "self" if s == me else f"cz_{match_id}_seat{s}",
            "agentName": f"seat{s}", "agentHandle": f"seat{s}",
            "status": status, "stackChips": stacks[s],
            "currentBetChips": street_bet[s], "totalCommittedChips": total[s],
            "payoutChips": None,
            "holeCards": [str(c) for c in state.hole_cards] if s == me else None,
        })

    my_bet = street_bet[me]
    current_bet = max(street_bet.values()) if street_bet else 0
    valid = set(state.valid_actions or [])
    can_raise_wire = "raise" in valid
    is_bet = can_raise_wire and current_bet == 0
    lo, hi = int(state.min_raise or 0), int(state.max_raise or 0)
    avail = [a for a in ("fold", "check", "call") if a in valid]
    if can_raise_wire:
        avail.append("bet" if is_bet else "raise")
        avail.append("all-in")
    elif "call" in valid and state.to_call >= state.your_stack:
        avail.append("all-in")
    allin_to = hi if can_raise_wire else (my_bet + min(state.to_call, state.your_stack))

    allowed = {
        "canFold": "fold" in valid, "canCheck": "check" in valid, "canCall": "call" in valid,
        "canBet": is_bet, "canRaise": can_raise_wire and not is_bet,
        "callAmount": int(state.to_call), "callChips": int(min(state.to_call, state.your_stack)),
        "callToAmount": (my_bet + int(state.to_call)) if "call" in valid else None,
        "minBet": lo if is_bet else None,
        "minRaiseTo": lo if (can_raise_wire and not is_bet) else None,
        "maxCommit": my_bet + int(state.your_stack),
        "allInToAmount": allin_to if "all-in" in avail else None,
        "betRange": {"min": lo, "max": hi} if is_bet else None,
        "raiseRange": {"min": lo, "max": hi} if (can_raise_wire and not is_bet) else None,
        "canAllIn": "all-in" in avail,
        "availableActions": avail,
        "amountSemantics": "toAmount", "amountHint": "", "reasoningRequired": False, "actionHint": "",
    }

    tid = f"{match_id}:{state.hand_number}"
    return {
        "id": tid, "tableId": tid, "tableNumber": 1, "competitionId": "chipzen",
        "status": "Active", "street": street,
        "potChips": int(state.pot), "currentBet": current_bet,
        "minRaiseTo": allowed["minRaiseTo"] or allowed["minBet"],
        "startedAt": 1, "endedAt": None, "countdownEndsAt": None, "actionDeadlineAt": None,
        "currentSeatNumber": _seat_num(me), "boardCards": [str(c) for c in state.board],
        "smallBlindChips": sb, "bigBlindChips": bb, "buyInChips": bb,
        "winners": [], "seats": seats,
        "actingSeatNumber": _seat_num(me), "selfSeatNumber": _seat_num(me),
        "allowedActions": allowed, "recentEvents": events,
    }


def to_chipzen_action(decision, state):
    """Translate decide_live()'s {'action','amount'} into a legal Chipzen Action."""
    from chipzen import Action

    valid = set(state.valid_actions or [])
    act = str(decision.get("action", "")).lower()
    amt = int(decision.get("amount", 0) or 0)
    lo, hi = int(state.min_raise or 0), int(state.max_raise or 0)

    if act in ("bet", "raise") and "raise" in valid:
        return Action.raise_to(max(lo, min(hi, amt)))
    if act in ("all-in", "allin"):
        if "raise" in valid:
            return Action.raise_to(hi)
        if "call" in valid:
            return Action.call()
    if act in valid and act in ("fold", "check", "call"):
        return getattr(Action, act)()
    # Anything illegal: never fold a free check.
    if "check" in valid:
        return Action.check()
    if act in ("bet", "raise") and "call" in valid:
        return Action.call()
    return Action.fold() if "fold" in valid else Action.call()


# --- Heads-up profile ------------------------------------------------------
# The core was tuned for 6-max. Heads-up, equity vs ONE random hand is high
# for almost anything, so it raised ~every hand preflop, built pots with junk,
# then the stack-risk / stalled / aggression brakes folded them away. This
# filter ONLY runs when there is exactly one opponent and ONLY touches
# preflop raises. decide_live itself is unchanged, so dev.fun is unaffected.
HU_PROFILE_ENABLED = True
HU_OPEN_MIN = 5.0    # Chen score to raise first-in (~top 40%: any pair, A-x good kicker, KTs, QJ...)
HU_3BET_MIN = 9.0    # Chen score to re-raise a raise (~top 10%: 99+, AQ+, AJs, KQs)
HU_DEFEND_MIN = 4.0  # Chen score to call a single raise instead of folding


def _chen(hole):
    try:
        import strategy as S
        return S._chen_score([S._parse_card(str(c)) for c in hole])
    except Exception:
        return 6.0


def hu_filter(decision, state, table):
    """Downgrade weak preflop raises heads-up. Returns (decision, changed_reason)."""
    if not HU_PROFILE_ENABLED or len(state.opponent_stacks) != 1 or (state.phase or "").lower() != "preflop":
        return decision, None
    act = str(decision.get("action", "")).lower()
    if act not in ("bet", "raise", "all-in", "allin"):
        return decision, None
    chen = _chen(state.hole_cards)
    bb = table.get("bigBlindChips") or 1
    facing_raise = table.get("currentBet", 0) > bb
    need = HU_3BET_MIN if facing_raise else HU_OPEN_MIN
    if chen >= need:
        return decision, None
    valid = set(state.valid_actions or [])
    if "check" in valid:
        new = "check"
    elif "call" in valid and (not facing_raise or chen >= HU_DEFEND_MIN) and state.to_call <= 3 * bb:
        new = "call"
    else:
        new = "fold"
    out = dict(decision, action=new, amount=0)
    out["_debug"] = dict(decision.get("_debug") or {}, decided_by=f"hu_filter_{new}",
                         hu_original=(decision.get("_debug") or {}).get("decided_by"), hu_chen=chen)
    return out, f"chen {chen} < {need}"


def decide_chipzen(state, game_config, match_id="cz"):
    table = to_devfun_table(state, game_config, match_id)
    decision = L.decide_live(table)
    decision, _ = hu_filter(decision, state, table)
    return to_chipzen_action(decision, state), decision
