"""Counterfactual for safeguard FOLDS (velocity_brake, stack_risk_cap).
For each logged fold facing a bet, look the hand up in hand_archive.jsonl
(all hole cards + board known) and compute what CALLING and checking it
down would have gained vs folding:  eq*pot - (1-eq)*to_call.
Opponents = aggressor + every seat that never folded this hand. Missing
board cards are sampled (all hole cards removed from the deck).
Caveat: assumes no further betting after the call."""
import ast, glob, json, os, random, re, sys
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, here); os.chdir(here)
import strategy as S
random.seed(7)
arch = {}
for l in open("hand_archive.jsonl", encoding="utf-8"):
    r = json.loads(l); arch[r["id"][:8]] = r
pat = re.compile(r"table=(\S+) street=(\S+) action=(\S+) amount=\S+ -> ok debug=(\{.*\})")
rows = []
for f in sorted(glob.glob("live_round_v*_chain.log")):
    v = int(re.search(r"v(\d+)_", f).group(1))
    for line in open(f, encoding="utf-8", errors="ignore"):
        if "velocity_brake" not in line and "stack_risk_cap" not in line: continue
        m = pat.search(line)
        if not m or m.group(3) != "fold": continue
        try: d = ast.literal_eval(m.group(4))
        except Exception: continue
        if d.get("decided_by") not in ("velocity_brake", "stack_risk_cap") or not d.get("to_call"): continue
        rows.append((v, m.group(1), d))
P = S._parse_card
out = {}; miss = 0
for v, tid, d in rows:
    h = arch.get(tid[:8])
    if not h: miss += 1; continue
    seats = h.get("seats", [])
    me = next((s for s in seats if "billy" in (s.get("agentHandle") or "").lower()), None)
    if not me: miss += 1; continue
    folded = {(e.get("summary") or {}).get("seatNumber") for e in h.get("recentEvents") or []
              if (e.get("summary") or {}).get("action") == "fold"}
    opp = [s for s in seats if s is not me and s.get("holeCards") and
           (s["seatNumber"] not in folded or s["seatNumber"] == d.get("aggressor_seat"))]
    if not opp: miss += 1; continue
    mine = [P(c) for c in me["holeCards"]]; board = [P(c) for c in (h.get("boardCards") or [])]
    oh = [[P(c) for c in s["holeCards"]] for s in opp]
    dead = set(mine) | set(board) | {c for x in oh for c in x}
    deck = [P(c) for c in S.FULL_DECK if P(c) not in dead]
    need = 5 - len(board); N = 1 if need == 0 else 3000; w = 0.0
    for _ in range(N):
        fb = board + random.sample(deck, need)
        mr = S._best_hand_rank(mine + fb); rs = [S._best_hand_rank(x + fb) for x in oh]; t = max(rs)
        w += 1 if mr > t else (1 / (1 + rs.count(t)) if mr == t else 0)
    eq = w / N; pot, tc = d.get("pot") or 0, d["to_call"]
    gain = eq * pot - (1 - eq) * tc
    k = (d["decided_by"], "S19" if v >= 126 else "pre-S19")
    out.setdefault(k, []).append((gain, eq, d.get("equity"), tc, pot, d.get("street")))
import builtins
for k in sorted(out):
    for x in sorted(out[k], key=lambda x:-abs(x[0]))[:3]: print("  top", k, [round(x[0]), round(x[1],2), x[2], x[3], x[4], x[5]])
print(f"fold rows {len(rows)}, unmatched/dropped {miss}")
for k in sorted(out):
    g = out[k]; n = len(g); tot = sum(x[0] for x in g)
    good = sum(1 for x in g if x[0] < 0)
    print(f"{k[0]:15} {k[1]:7} n={n:3}  call-instead gain total={tot:+8.0f}  per fold={tot/n:+7.1f}"
          f"  fold was right {good}/{n}  mean true eq={sum(x[1] for x in g)/n:.2f}  mean modeled eq={sum((x[2] or 0) for x in g)/n:.2f}")
    by = {}
    for x in g: by.setdefault(x[5], []).append(x[0])
    print("      by street:", {s: (len(v), round(sum(v))) for s, v in by.items()})
