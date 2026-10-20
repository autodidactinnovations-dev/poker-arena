"""Redesign step 1 (TEST ONLY): decide with range-based equity.

When the bot faces a bet after the flop, it normally judges its hand against
random cards (minus an aggression discount). This mode instead uses
strategy.estimate_equity_ranged - the existing "S22" calculation that is
already computed and logged on dev.fun but never acted on - which judges the
hand against the hands an opponent's betting actually suggests.

Nothing in live_agent.py or strategy.py is changed. install() wraps
decide_live for the current process only (used by sim.py --ranged).
"""
import live_agent as L
import strategy as S

_orig_decide = L.decide_live
_orig_equity = S.estimate_equity


def _ranged_for(table):
    if table.get("street") not in ("Flop", "Turn", "River"):
        return None
    allowed = table.get("allowedActions") or {}
    if not (allowed.get("callAmount") or 0) > 0:
        return None
    me = S._find_self_seat(table)
    my_num = me.get("seatNumber")
    hole = [S._parse_card(c) for c in (me.get("holeCards") or [])]
    board = [S._parse_card(c) for c in (table.get("boardCards") or [])]
    acts = S.opponent_postflop_actions(table, my_num)
    live = [s for s in table.get("seats") or [] if s is not me and s.get("status") in ("Active", "AllIn")]
    certain = [acts[s["seatNumber"]] for s in live if s.get("seatNumber") in acts]
    pending = len(live) - len(certain)
    if not certain:
        return None
    eq, _acc = S.estimate_equity_ranged(hole, board, certain, pending_opponents=pending)
    return eq


def decide_live_ranged(table):
    try:
        eq = _ranged_for(table)
    except Exception:
        eq = None
    if eq is None:
        return _orig_decide(table)
    S.estimate_equity = lambda *a, **k: eq
    try:
        out = _orig_decide(table)
        (out.setdefault("_debug", {}))["ranged_equity"] = round(eq, 3)
        return out
    finally:
        S.estimate_equity = _orig_equity


def install():
    L.decide_live = decide_live_ranged
