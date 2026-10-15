"""Do we fold too often after being re-raised? (dev.fun, read-only analysis)

Spot: on a street, we bet/raised, then faced a raise back (to_call > 0) on the
SAME street. For each such decision: what we did, and - for folds - whether
our hand was actually ahead of the raiser (equity vs his REAL hole cards from
hand_archive, rolled out over the remaining board).
Usage: py analysis/reraise_folds.py [first_version]   (default 180)
"""
import ast, glob, json, random, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy as S

first = int(sys.argv[1]) if len(sys.argv) > 1 else 180
arch = {}
for line in open("hand_archive.jsonl", encoding="utf-8"):
    try:
        h = json.loads(line); arch[h["id"][:8]] = h
    except Exception:
        pass

pat = re.compile(r"table=(\w+) street=(\w+) action=(\S+) amount=(\d+).*?debug=(\{.*\})\s*$")
rows = []
for f in sorted(glob.glob("live_round_v*_chain.log")):
    v = int(re.search(r"_v(\d+)_", f).group(1))
    if v < first:
        continue
    last = {}
    for line in open(f, encoding="utf-8", errors="ignore"):
        m = pat.search(line)
        if not m:
            continue
        tid, street, act = m.group(1), m.group(2), m.group(3)
        try:
            d = ast.literal_eval(m.group(5))
        except Exception:
            continue
        key = (tid, street)
        if last.get(key) in ("bet", "raise", "all-in") and (d.get("to_call") or 0) > 0:
            rows.append((v, tid, street, act, d))
        last[key] = act

def eq_vs(hole, opp, board, n=400):
    deck = [c for c in S.FULL_DECK if c not in hole + opp + board]
    w = 0.0
    for _ in range(n):
        b = board + random.sample(deck, 5 - len(board))
        a = S._best_hand_rank([S._parse_card(c) for c in hole + b])
        o = S._best_hand_rank([S._parse_card(c) for c in opp + b])
        w += 1 if a > o else (0.5 if a == o else 0)
    return w / n

from collections import Counter
print(f"logs v{first}+ : {len(rows)} spots where we bet/raised and got raised back")
print("our response:", Counter(r[3] for r in rows))
print("by street:", Counter((r[2], r[3]) for r in rows))
print("decided_by on folds:", Counter(r[4].get("decided_by") for r in rows if r[3] == "fold").most_common(8))

ahead, n_eval, sample = 0, 0, []
for v, tid, street, act, d in rows:
    if act != "fold":
        continue
    h = arch.get(tid)
    if not h:
        continue
    seat = d.get("aggressor_seat")
    opp = next((s.get("holeCards") for s in h["seats"] if s.get("seatNumber") == seat), None)
    hole, board = d.get("hole") or [], d.get("board") or []
    if not opp or len(opp) != 2 or len(hole) != 2:
        continue
    e = eq_vs(hole, opp, board)
    n_eval += 1
    ahead += e > 0.5
    sample.append((round(e, 2), street, hole, board, opp, d.get("pot"), d.get("to_call"), d.get("decided_by")))
if n_eval:
    print(f"\nfolds with raiser's cards known: {n_eval}; we were AHEAD in {ahead} ({ahead/n_eval:.0%})")
    print("avg equity we gave up:", round(sum(s[0] for s in sample) / n_eval, 3))
    for s in sorted(sample, reverse=True)[:12]:
        print("  eq", s[0], s[1], "us", s[2], "board", s[3], "them", s[4], "pot", s[5], "call", s[6], s[7])
