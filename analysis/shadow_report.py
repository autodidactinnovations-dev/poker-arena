"""19 shadow log reader. S19-only, filtered on the DATA's competition_id.

A settled runout is NOT "what the call would have returned" - our continued
presence would have changed the hand. Unresolved records are expected to
dominate. This bounds the question; it does not price the rule.
"""
import collections, json, os

PATH = "C:/Users/billy/poker-arena/shadow_aggression_fold.jsonl"
S19 = "cmtwz5y2z2u4k13ubquaqff8w"

if not os.path.exists(PATH):
    print("no shadow file yet")
    raise SystemExit(0)

folds, settled = {}, {}
for line in open(PATH, encoding="utf-8"):
    try: r = json.loads(line)
    except Exception: continue
    if r.get("stage") == "fold":
        if r.get("competition_id") == S19:
            folds[r["hand_id"]] = r
    elif r.get("stage") == "settled":
        settled[r["hand_id"]] = r

joined = {h: (f, settled[h]) for h, f in folds.items() if h in settled}
ran = [h for h, (f, s) in joined.items() if s.get("board_ran_out")]
print("refusals recorded : %d" % len(folds))
print("with settlement   : %d   (unresolved %d)" % (len(joined), len(folds) - len(joined)))
sa = collections.Counter(f.get("shadow_action") for f in folds.values())
print("shadow_action     : %s" % dict(sa))
print("of the settled, boards that RAN OUT: %d  <- the only sample worth having"
      % len(ran))
