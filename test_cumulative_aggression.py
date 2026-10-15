"""Verification for the cumulative-aggression rebuild of range_strength.

The three S17 losing hands are RECONSTRUCTED from live_round_v15_s17.log, not
replayed from stored payloads (the raw table JSON was not retained). Each
villain action is inferred from the logged `opp_raises_this_street` deltas and
our own logged actions, so the exact scores are approximate - what is being
asserted is DIRECTION and MONOTONICITY, which is what the rebuild is about.

Old behaviour being regression-tested against, from the real logs:
  cmtewk95  rs went 0.0 -> 0.0 -> 0.35 -> 0.70 -> 0.35   <- RESET into the turn
Requirement: range_strength must never decrease while a villain keeps applying
pressure.
"""
import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import strategy as S

failures = []
ME = 5


def ev(seq, street, action, seat):
    return {"id": f"e{seq}", "sequence": seq, "type": "ActionTaken",
            "street": street, "summary": {"action": action, "seatNumber": seat}}


def progression(events_by_step):
    """Feed cumulatively larger event prefixes; return (score, rs) per step."""
    out = []
    for prefix in events_by_step:
        table = {"recentEvents": prefix}
        sc = S.cumulative_aggression(table, ME)
        out.append((round(sc, 2), round(S.aggression_to_range_strength(sc), 3)))
    return out


# --- cmtewk95 (-421): villain raised preflop, raised twice on the flop, then
# raised our turn bet. Old code showed 0.70 on the flop then FELL to 0.35 on
# the turn. ---
e = []
steps = []
e = e + [ev(1, "Preflop", "raise", 2)];                     steps.append(list(e))
e = e + [ev(2, "Flop", "raise", 2)];                        steps.append(list(e))
e = e + [ev(3, "Flop", "raise", 2)];                        steps.append(list(e))
e = e + [ev(4, "Turn", "call", 2)];                         steps.append(list(e))
e = e + [ev(5, "Turn", "raise", 2)];                        steps.append(list(e))
prog = progression(steps)
print("cmtewk95 (-421)  score/rs by step:", prog)
rs_seq = [p[1] for p in prog]
if any(rs_seq[i + 1] < rs_seq[i] for i in range(len(rs_seq) - 1)):
    failures.append(f"cmtewk95: range_strength DECREASED across streets: {rs_seq}")
# The specific regression: the turn must not be weaker than the flop was.
if rs_seq[3] < rs_seq[2]:
    failures.append(f"cmtewk95: turn discount {rs_seq[3]} < flop discount {rs_seq[2]} (the reset bug)")
if not rs_seq[-1] > 0.55:
    failures.append(f"cmtewk95: sustained multi-street aggression should approach the cap, got {rs_seq[-1]}")

# --- cmtey7te (-129) and cmtf1w8f (-123): villain BET rather than raised on
# several streets. Old code scored these as ZERO aggression. ---
e, steps = [], []
e = e + [ev(1, "Preflop", "raise", 3)];                     steps.append(list(e))
e = e + [ev(2, "Flop", "bet", 3)];                          steps.append(list(e))
e = e + [ev(3, "Turn", "bet", 3)];                          steps.append(list(e))
e = e + [ev(4, "River", "bet", 3)];                         steps.append(list(e))
prog = progression(steps)
print("bet-only villain  score/rs by step:", prog)
rs_seq = [p[1] for p in prog]
if any(rs_seq[i + 1] < rs_seq[i] for i in range(len(rs_seq) - 1)):
    failures.append(f"bet-only: range_strength DECREASED: {rs_seq}")
if rs_seq[1] <= 0.0:
    failures.append("bet-only: a flop BET must register aggression (old code scored it 0.0)")
if not rs_seq[-1] > 0.50:
    failures.append(f"bet-only: three streets of betting should be a strong discount, got {rs_seq[-1]}")

# --- Passive villain: calls only. Should register something, but far less
# than an aggressive line. Preserves the original continuation signal. ---
e, steps = [], []
e = e + [ev(1, "Flop", "call", 4)];                         steps.append(list(e))
e = e + [ev(2, "Turn", "call", 4)];                         steps.append(list(e))
prog = progression(steps)
print("passive caller    score/rs by step:", prog)
passive_rs = prog[-1][1]
if passive_rs <= 0.0:
    failures.append("passive: calling across streets should still register (original signal)")
if passive_rs > 0.35:
    failures.append(f"passive: pure calling should be WEAK evidence, got {passive_rs}")

# --- Our own actions must never count as opponent aggression ---
self_only = {"recentEvents": [ev(1, "Flop", "raise", ME), ev(2, "Turn", "raise", ME)]}
if S.cumulative_aggression(self_only, ME) != 0.0:
    failures.append("our own raises must not count as opponent aggression")

# --- Checks/folds add nothing, and never decrement ---
base = [ev(1, "Flop", "raise", 2)]
after_check = base + [ev(2, "Turn", "check", 2), ev(3, "River", "fold", 2)]
s_base = S.cumulative_aggression({"recentEvents": base}, ME)
s_after = S.cumulative_aggression({"recentEvents": after_check}, ME)
print(f"check/fold do not decrement: {s_base} -> {s_after}")
if s_after < s_base:
    failures.append("checks/folds must not decrement accumulated aggression")

# --- Street weighting: same action later in the hand must count for more ---
pre = S.cumulative_aggression({"recentEvents": [ev(1, "Preflop", "raise", 2)]}, ME)
riv = S.cumulative_aggression({"recentEvents": [ev(1, "River", "raise", 2)]}, ME)
print(f"street weighting: preflop raise={pre} river raise={riv}")
if not riv > pre:
    failures.append("a river raise should outweigh a preflop raise (ranges narrow)")

print()
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PASS: aggression accumulates monotonically, bets now count, later streets "
      "weigh more, passive continuation stays weak, own actions excluded")
