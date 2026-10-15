"""S19-ONLY primary signals. Lives in the PROJECT dir, not the scratchpad.

WHY HERE. The original lived in the session scratchpad and was deleted by temp
cleanup after three idle weeks, taking the per-run reporting with it. The DATA
survived (it is in the project dir); only the tooling vanished. Reporting
infrastructure has to be as durable as the data it reads.

S19 starts at run 126; anything earlier is a different pool, field and bankroll
and must never be pooled with it.
"""
import ast, collections, glob, json, re

FIRST_S19_RUN = 126
ARCHIVE = "C:/Users/billy/poker-arena/hand_archive.jsonl"
LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] table=(\S+) street=(\S+) action=(\S+) "
                  r"amount=(-?\d+) -> ok debug=(\{.*\})$")
RUN = re.compile(r"live_round_v(\d+)_chain\.log$")

delta = {}
for line in open(ARCHIVE, encoding="utf-8"):
    try: t = json.loads(line)
    except Exception: continue
    if t.get("_delta_source") == "replays" and t.get("chipDelta") is not None:
        delta[t["id"][:8]] = t["chipDelta"]

seq = collections.defaultdict(list)
runs = []
for path in sorted(glob.glob("C:/Users/billy/poker-arena/live_round_v*.log")):
    m = RUN.search(path.replace("\\", "/"))
    if not m or int(m.group(1)) < FIRST_S19_RUN:
        continue
    runs.append(int(m.group(1)))
    for line in open(path, encoding="utf-8", errors="replace"):
        mm = LINE.match(line.rstrip("\n"))
        if not mm: continue
        try: d = ast.literal_eval(mm.group(6))
        except Exception: continue
        seq[mm.group(2)].append({"street": mm.group(3), "act": mm.group(4),
                                 "amt": int(mm.group(5)), "d": d})

print("S19 runs included: %s-%s (%d logs)"
      % (min(runs), max(runs), len(set(runs))) if runs else "no S19 runs")
J = [(t, delta[t], v) for t, v in seq.items() if t in delta]
print("decisions %d over %d tables; attributable hands %d, net %+d"
      % (sum(len(v) for v in seq.values()), len(seq), len(J),
         sum(x[1] for x in J)))

fires = [x for v in seq.values() for x in v
         if x["d"].get("decided_by") == "aggression_fold"]
print("aggression_fold firings: %d" % len(fires))

poc = [(t, dl) for t, dl, v in J
       if max(v, key=lambda x: x["amt"])["d"].get("decided_by") == "pot_odds_call"]
print("pot_odds_call hands: %d, net %+d" % (len(poc), sum(x[1] for x in poc)))
