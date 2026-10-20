"""
Local poker simulator - test the bot on your own laptop, no arena needed.

Plays No-Limit Hold'em between YOUR bot (the real decide_live, via the same
chipzen_adapter used on Chipzen - nothing reimplemented) and a set of simple
opponent styles. Every hand resets stacks to 100 big blinds, so results are
comparable run to run. Reports your bot's win rate in bb/100 (big blinds won
per 100 hands) with an error bar.

    py sim.py                         # 6-max, 500 hands, mixed field
    py sim.py --hands 2000 --players 2 --opps tag      # heads-up vs a TAG
    py sim.py --opps station,station,lag,nit,maniac     # pick the field

Opponent styles: nit, tag, lag, station, maniac, random.
These are PRACTICE opponents, not the dev.fun or Chipzen field - use the sim to
compare two versions of the bot against the same field, then confirm live.
Results go to sim_results.jsonl (git-ignored via *.jsonl).
"""

import argparse
import io
import json
import math
import random
import sys
import time
from contextlib import redirect_stdout

from chipzen import GameState

with redirect_stdout(io.StringIO()):
    import chipzen_adapter as A
import strategy as S
import fast_eval
fast_eval.install()   # identical results, faster equity sims (see test_fast_eval.py)

BB, SB, STACK = 100, 50, 10000
HERO_HOOK = None   # set by sim_diag.py to inspect each bot decision
PHASES = ["preflop", "flop", "turn", "river"]


# ---------------------------------------------------------------- opponents
def _strength(hole, board, n_opp):
    if not board:
        return min(1.0, S._chen_score([S._parse_card(c) for c in hole]) / 20.0 + 0.15)
    return S.estimate_equity([S._parse_card(c) for c in hole], [S._parse_card(c) for c in board],
                             num_opponents=max(1, n_opp), trials=60)


def make_opp(style, rng):
    # (play_threshold, raise_threshold, bluff_rate, call_down_bonus)
    P = {"nit": (0.62, 0.78, 0.00, -0.05), "tag": (0.50, 0.66, 0.06, 0.00),
         "lag": (0.38, 0.55, 0.18, 0.05), "station": (0.30, 0.85, 0.02, 0.30),
         "maniac": (0.25, 0.40, 0.40, 0.10)}

    def act(v):
        if style == "random":
            a = rng.choice(v["valid"])
            return (a, rng.randint(v["min_raise"], max(v["min_raise"], v["max_raise"]))) if a == "raise" else (a, 0)
        play, rais, bluff, cdb = P[style]
        s = _strength(v["hole"], v["board"], v["n_opp"])
        pot_odds = v["to_call"] / (v["pot"] + v["to_call"]) if v["to_call"] else 0
        can_raise = "raise" in v["valid"]
        if can_raise and (s >= rais or rng.random() < bluff):
            size = v["current_bet"] + int((v["pot"] + v["to_call"]) * rng.choice([0.5, 0.75, 1.0]))
            return "raise", max(v["min_raise"], min(v["max_raise"], size))
        if v["to_call"] == 0:
            return "check", 0
        need = max(pot_odds, play if not v["board"] else 0) - cdb
        return ("call", 0) if s >= need else ("fold", 0)
    return act


# ---------------------------------------------------------------- engine
def play_hand(n, hero_seat, opps, button, rng, hand_no):
    deck = [r + s for r in "23456789TJQKA" for s in "cdhs"]
    rng.shuffle(deck)
    holes = [[deck.pop(), deck.pop()] for _ in range(n)]
    board_all = [deck.pop() for _ in range(5)]
    stack = [STACK] * n
    total = [0] * n
    folded = set()
    history = []
    if n == 2:
        sb_seat, bb_seat = button, (button + 1) % n
    else:
        sb_seat, bb_seat = (button + 1) % n, (button + 2) % n

    def put(seat, amt):
        amt = min(amt, stack[seat])
        stack[seat] -= amt
        total[seat] += amt
        return amt

    for phase_i, phase in enumerate(PHASES):
        board = board_all[: [0, 3, 4, 5][phase_i]]
        street = [0] * n
        if phase == "preflop":
            street[sb_seat] = put(sb_seat, SB)
            history.append({"seat": sb_seat, "action": "post_small_blind", "amount": street[sb_seat], "phase": phase, "is_timeout": False})
            street[bb_seat] = put(bb_seat, BB)
            history.append({"seat": bb_seat, "action": "post_big_blind", "amount": street[bb_seat], "phase": phase, "is_timeout": False})
            first = (bb_seat + 1) % n
        else:
            first = (button + 1) % n if n > 2 else bb_seat
        current, last_raise = max(street), BB
        live = [s for s in range(n) if s not in folded]
        if len(live) < 2:
            break
        # Seats that still need to act this street.
        order = [(first + i) % n for i in range(n)]
        pending = [s for s in order if s not in folded and stack[s] > 0]
        while pending:
            seat = pending.pop(0)
            if seat in folded or stack[seat] == 0:
                continue
            if len([s for s in range(n) if s not in folded]) < 2:
                break
            to_call = min(current - street[seat], stack[seat])
            min_raise = current + last_raise
            max_raise = street[seat] + stack[seat]
            valid = (["check"] if to_call == 0 else ["fold", "call"])
            others_can_act = any(stack[s] > 0 for s in range(n) if s != seat and s not in folded)
            if max_raise > current and others_can_act:
                valid.append("raise")
            min_raise = min(min_raise, max_raise)
            pot = sum(total)
            if seat == hero_seat:
                gs = GameState.from_turn_request({"valid_actions": valid, "request_id": "r", "round_id": f"h{hand_no}",
                    "state": {"hand_number": hand_no, "phase": phase, "your_hole_cards": holes[seat], "board": board,
                              "pot": pot, "your_stack": stack[seat],
                              "opponent_stacks": [stack[s] for s in range(n) if s != seat],
                              "your_seat": seat, "dealer_seat": button, "to_call": to_call,
                              "min_raise": min_raise, "max_raise": max_raise, "action_history": list(history)}})
                act_obj, _dec = A.decide_chipzen(gs, {"small_blind": SB, "big_blind": BB, "num_players": n}, "sim")
                act, amt = act_obj.action, act_obj.amount
                if HERO_HOOK:
                    HERO_HOOK(_dec, act, gs, holes, [s for s in range(n) if s not in folded and s != seat], board_all, board)
            else:
                act, amt = opps[seat]({"hole": holes[seat], "board": board, "pot": pot, "to_call": to_call,
                                       "current_bet": current, "min_raise": min_raise, "max_raise": max_raise,
                                       "valid": valid, "n_opp": len([s for s in range(n) if s not in folded]) - 1})
            if act not in valid:
                act = "check" if "check" in valid else "fold"
            if act == "fold":
                folded.add(seat)
                history.append({"seat": seat, "action": "fold", "amount": 0, "phase": phase, "is_timeout": False})
            elif act == "check":
                history.append({"seat": seat, "action": "check", "amount": 0, "phase": phase, "is_timeout": False})
            elif act == "call":
                added = put(seat, to_call)
                street[seat] += added
                history.append({"seat": seat, "action": "call", "amount": added, "phase": phase, "is_timeout": False})
            else:  # raise to amt
                amt = max(min_raise, min(max_raise, int(amt)))
                added = put(seat, amt - street[seat])
                street[seat] += added
                if street[seat] - current >= last_raise:
                    last_raise = street[seat] - current
                current = max(current, street[seat])
                history.append({"seat": seat, "action": "raise", "amount": street[seat], "phase": phase, "is_timeout": False})
                pending = [(seat + i) % n for i in range(1, n)]
                pending = [s for s in pending if s not in folded and stack[s] > 0]
        if len([s for s in range(n) if s not in folded]) < 2:
            break

    # Showdown with side pots.
    contenders = [s for s in range(n) if s not in folded]
    payout = [0] * n
    if len(contenders) == 1:
        payout[contenders[0]] = sum(total)
    else:
        rank = {s: S._best_hand_rank([S._parse_card(c) for c in holes[s] + board_all]) for s in contenders}
        levels = sorted(set(total[s] for s in contenders))
        prev = 0
        for lv in levels:
            layer = sum(min(total[s], lv) - min(total[s], prev) for s in range(n))
            elig = [s for s in contenders if total[s] >= lv]
            best = max(rank[s] for s in elig)
            winners = [s for s in elig if rank[s] == best]
            for w in winners:
                payout[w] += layer // len(winners)
            payout[winners[0]] += layer % len(winners)
            prev = lv
        # Chips committed above the top contender level go back to their owner.
        top = levels[-1]
        for s in range(n):
            if total[s] > top:
                payout[s] += total[s] - top
    assert sum(payout) == sum(total), (payout, total)   # chips conserved
    return payout[hero_seat] - total[hero_seat]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", type=int, default=500)
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--opps", default="tag,lag,station,nit,maniac")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-hu-filter", action="store_true", help="turn off the Chipzen heads-up preflop filter")
    ap.add_argument("--no-aggfold", action="store_true", help="switch off live_agent's aggression_fold rule")
    a = ap.parse_args()
    if a.no_aggfold:
        A.L.AGGRESSION_FOLD_ENABLED = False
    A.HU_PROFILE_ENABLED = not a.no_hu_filter
    rng = random.Random(a.seed)
    random.seed(a.seed)
    styles = a.opps.split(",")
    n = a.players
    results, t0 = [], time.time()
    for h in range(a.hands):
        hero = h % n                      # hero rotates through every seat
        opps = {}
        others = [s for s in range(n) if s != hero]
        for i, s in enumerate(others):
            opps[s] = make_opp(styles[i % len(styles)], rng)
        with redirect_stdout(io.StringIO()):
            results.append(play_hand(n, hero, opps, h % n, rng, h + 1))
        if (h + 1) % 100 == 0:
            m = sum(results) / len(results) / BB * 100
            print(f"  {h + 1} hands  {m:+.1f} bb/100  ({time.time() - t0:.0f}s)", flush=True)
    bb = [r / BB for r in results]
    mean = sum(bb) / len(bb)
    sd = math.sqrt(sum((x - mean) ** 2 for x in bb) / max(1, len(bb) - 1))
    se = sd / math.sqrt(len(bb)) * 100
    print(f"\nRESULT  {len(bb)} hands, {n} players vs [{a.opps}]")
    print(f"  bot win rate: {mean * 100:+.1f} bb/100   (+/- {1.96 * se:.1f} at 95%)")
    print(f"  time: {time.time() - t0:.0f}s  ({len(bb) / max(1e-9, time.time() - t0) * 60:.0f} hands/min)")
    with open("sim_results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "hands": len(bb), "players": n,
                            "opps": a.opps, "seed": a.seed, "hu_filter": A.HU_PROFILE_ENABLED, "aggfold": A.L.AGGRESSION_FOLD_ENABLED,
                            "bb100": round(mean * 100, 2), "ci95": round(1.96 * se, 2)}) + "\n")


if __name__ == "__main__":
    main()
