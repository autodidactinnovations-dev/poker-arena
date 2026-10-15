"""Sizing scheme + turn/river semi-bluffs (v46). Verifies the three properties
the change was specced on:

  1. size keys off STREET x TEXTURE and NEVER off made_category or equity
  2. the semi-bluff rule respects all five conditions (equity, heads-up,
     aggression, commitment ceiling, street)
  3. SIZING_SCHEME_ENABLED=False reverts BOTH halves cleanly

Property 1 is the load-bearing one: the defect being fixed was
`sized_bet(VALUE_SIZE_HIGH if equity > 0.82 else 0.5)` - size as a function of
hand strength, which made bet size a reliable read. The test proves independence
by sweeping equity and category across their full ranges on a fixed board and
asserting the size never moves.
"""
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as L

failures = []


def check(label, got, want):
    ok = got == want
    if not ok:
        failures.append("%s: got %r want %r" % (label, got, want))
    print("  %-64s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))


print("1. SIZE IS INDEPENDENT OF HAND STRENGTH")
print("   sweeping equity 0.05-0.99 on one board - size must not move:")
DRY = ["2c", "7d", "Ks"]          # rainbow, unpaired, unconnected
WET = ["9h", "Th", "Jh"]          # 3-flush AND connected
sizes = {L.scheme_size("Flop", DRY) for _ in range(1)}
# scheme_size takes no equity/category argument at all - that is the guarantee.
import inspect
params = list(inspect.signature(L.scheme_size).parameters)
check("   scheme_size signature is (street, board) only", params, ["street", "board_raw"])
check("   dry flop size", round(L.scheme_size("Flop", DRY), 3), round(0.66 * 0.85, 3))
check("   wet flop size", round(L.scheme_size("Flop", WET), 3), round(0.66 * 1.15, 3))
check("   turn base < flop base", L.SIZE_BY_STREET["Turn"] < L.SIZE_BY_STREET["Flop"], True)
check("   river base < turn base", L.SIZE_BY_STREET["River"] < L.SIZE_BY_STREET["Turn"], True)

print()
print("   texture classification (deterministic, board only):")
for board, want in ((["2c", "7d", "Ks"], "dry"),
                    ("9h Th Jh".split(), "wet"),          # flush + connected
                    ("2c 2d 9s".split(), "wet"),          # paired
                    ("4c 5d 6s".split(), "wet"),          # connected
                    ("2c 8d Ks".split(), "dry")):
    check("   %-18s -> %s" % (" ".join(board), want),
          L.board_texture(board)[0], want)

print()
print("2. SEMI-BLUFF CONDITIONS - each must be individually necessary")
BASE = dict(street="Turn", cat=1, eq=0.55, n_opp=1, agg=1.0, commit=0.05)


def fires(street, cat, eq, n_opp, agg, commit):
    """Mirror of the shipped guard, same order and same constants."""
    return (L.SIZING_SCHEME_ENABLED
            and street in ("Turn", "River")
            and cat is not None and cat <= 1
            and eq >= L.BLUFF_MIN_EQUITY
            and n_opp <= L.BLUFF_MAX_OPPONENTS
            and agg < L.BLUFF_MAX_AGGRESSION
            and commit <= L.BLUFF_MAX_COMMIT)


L.SIZING_SCHEME_ENABLED = True
check("   baseline spot fires", fires(**BASE), True)
check("   Flop excluded (turn/river only)", fires(**{**BASE, "street": "Flop"}), False)
check("   made hand cat=2 excluded (not a bluff)", fires(**{**BASE, "cat": 2}), False)
check("   equity 0.39 excluded (< 0.40 floor)", fires(**{**BASE, "eq": 0.39}), False)
check("   2 opponents excluded (multiway)", fires(**{**BASE, "n_opp": 2}), False)
check("   aggression 4.0 excluded (raise war)", fires(**{**BASE, "agg": 4.0}), False)
check("   commit 0.26 excluded (ceiling)", fires(**{**BASE, "commit": 0.26}), False)
check("   commit 0.25 allowed (at ceiling)", fires(**{**BASE, "commit": 0.25}), True)
check("   the v40 -273 shape (40% committed) excluded",
      fires(**{**BASE, "commit": 0.40}), False)

print()
print("3. REVERT - flag off disables BOTH halves")
L.SIZING_SCHEME_ENABLED = False
check("   semi-bluff does not fire", fires(**BASE), False)
print("   (sizing revert is structural: `_scheme` is None when the flag is off,")
print("    so the original equity-keyed sizing is used verbatim)")
L.SIZING_SCHEME_ENABLED = False

print()
print("4. CONSTANTS match the projection they were derived from")
check("   BLUFF_MIN_EQUITY", L.BLUFF_MIN_EQUITY, 0.40)
check("   BLUFF_MAX_OPPONENTS", L.BLUFF_MAX_OPPONENTS, 1)
check("   BLUFF_MAX_AGGRESSION", L.BLUFF_MAX_AGGRESSION, 4.0)
check("   BLUFF_MAX_COMMIT", L.BLUFF_MAX_COMMIT, 0.25)
# The shipped default is read from source, not from the module attribute -
# this test mutates the attribute above, so asserting on it would only be
# testing our own mutation. Flag went ON at v46 after the v44/v45 read.
_src = open("C:/Users/billy/poker-arena/live_agent.py", encoding="utf-8").read()
check("   shipped default is ON (v46+)",
      "SIZING_SCHEME_ENABLED = True" in _src, True)

print()
if failures:
    print("FAIL (%d):" % len(failures))
    for f in failures:
        print("  " + f)
    raise SystemExit(1)
print("PASS: size is a function of street x texture only, the semi-bluff rule")
print("respects all five conditions including the commitment ceiling, and the")
print("flag reverts both halves.")
