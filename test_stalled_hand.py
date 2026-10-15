"""Regression suite for the stalled-hand guard.

Built from the 7 medium losses traced out of 178 archived hands, which were
79% of all loss chips. Every case below uses the REAL logged values (street,
made_category, equity, to_call) from those hands.

Two halves, as always:
  SHOULD FOLD    - the 5 leak hands, each a turn call on literally nothing.
  SHOULD NOT FIRE - the +42 winner (a pair, category 1), the legitimate bad
                    beat, checks, raises, and early streets.
"""
import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as LA

failures = []
F = LA.stalled_hand_fold

# --- SHOULD FOLD: the 5 stopped leak hands, real logged values ---
leaks = [
    # (table, street, made_category, equity, to_call, chips_lost)
    ("cmtgqw4x", "Turn", 0, 0.369, 32, -48),
    ("cmtgt8rn", "Turn", 0, 0.253, 16, -29),
    ("cmtgpv0o", "Turn", 0, 0.219, 12, -27),
    ("cmtgtgu1", "Turn", 0, 0.244, 11, -21),
    ("cmtgq2yp", "Turn", 0, 0.435, 7, -15),
]
for tbl, st, cat, eq, tc, lost in leaks:
    got = F(st, cat, eq, tc)
    print(f"LEAK {tbl} {st} cat={cat} eq={eq} to_call={tc} (lost {lost}) -> fold={got}")
    if not got:
        failures.append(f"{tbl}: guard must fold this call (lost {lost})")

# --- SHOULD NOT FIRE: the +42 winner. Pair (category 1), below the release
# valve on equity, so ONLY the category boundary protects it. Widening
# STALLED_MAX_CATEGORY to 1 would destroy this hand for zero extra savings. ---
got = F("Turn", 1, 0.34, 16)
print(f"\nWINNER cmtgt3hp Turn cat=1 eq=0.34 to_call=16 (won +42) -> fold={got}")
if got:
    failures.append("cmtgt3hp: guard must NOT fold a pair - it won +42")

# --- Release valve: high card but real equity (a live draw) gets through ---
for eq in (0.45, 0.50, 0.80):
    got = F("Turn", 0, eq, 20)
    print(f"release valve: cat=0 eq={eq} -> fold={got}")
    if got:
        failures.append(f"release valve should let equity {eq} through")
got = F("Turn", 0, 0.44, 20)
if not got:
    failures.append("equity just below the valve (0.44) should still fold")

# --- Checking is NEVER touched. None of the leak hands lost a chip checking,
# and folding when checking is free throws away all equity for nothing. ---
print()
for cat in (0, 1, 2, 3, 4, 5):
    for eq in (0.0, 0.2, 0.44, 0.9):
        if F("Turn", cat, eq, 0) or F("River", cat, eq, 0):
            failures.append(f"guard fired with to_call=0 (a CHECK) at cat={cat} eq={eq}")
print("invariant: never fires when to_call == 0 (a check) -> checked "
      "across categories 0-5 x equity {0.0,0.2,0.44,0.9}")

# --- Street gating: preflop and flop are out of scope. The flop is where
# cmtgsl03 (-18) leaked, and extending there was deliberately NOT done -
# it would widen exposure well beyond the traced evidence. ---
for st in ("Preflop", "Flop"):
    if F(st, 0, 0.20, 20):
        failures.append(f"guard must not fire on {st}")
print("street gating: Preflop/Flop never fire -> checked")

# --- Made hands are out of scope entirely, at any equity ---
for cat in (1, 2, 3, 4, 5, 6, 7, 8):
    if F("River", cat, 0.10, 50):
        failures.append(f"guard must not fire on made category {cat}")
print("made hands (cat 1-8) never fire -> checked")

# --- cmtgtore: the legitimate bad beat. It held high card on the turn and
# made a straight on the river, escaping only because it faced no turn bet.
# This documents the KNOWN drawing-hand risk: had it been bet into on the
# turn at its logged 0.184 equity, the guard WOULD have folded a hand that
# went on to make a straight. The release valve does not save it - 0.184 is
# far below 0.45. Asserting the real behaviour rather than pretending
# otherwise. ---
turn_eq = 0.184
would = F("Turn", 0, turn_eq, 20)
print(f"\ncmtgtore drawing-hand risk: turn cat=0 eq={turn_eq} faced a bet -> fold={would}")
if not would:
    failures.append("test assumption wrong: cmtgtore's turn state should trip the guard")
print("  ^ KNOWN LIMIT, documented: guard cannot see outs. It escaped live only")
print("    because it faced no turn bet. Revisit if archived draws start folding.")

print()
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PASS: folds all 5 traced leak calls, spares the +42 pair, respects the "
      "release valve, and never touches a check")
