"""§24 fit: P(an opponent is AHEAD of hand-class H | street, their action+size).
Every pair (actor O betting/raising/calling postflop, other live player H) in
hand_archive.jsonl is one sample; label = O's made hand beats H's on the board
at that moment. Pot is rebuilt from blind/bet/raise/call amounts.
Usage: python fit_beat_rates.py [exclude_ids_file]  -> writes analysis/beat_rates.json"""
import json, os, sys, collections
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, here); os.chdir(here)
import strategy as S
P = S._parse_card
ORDER = ["Preflop", "Flop", "Turn", "River"]; BL = {"Flop": 3, "Turn": 4, "River": 5}
RANKS = "23456789TJQKA"

def h_class(hole, board):
    rk = S._best_hand_rank(hole + board); cat = rk[0]
    if cat == 0: return "air"
    if cat >= 3: return "trips+"
    if cat == 2: return "two_pair"
    top = max(c[0] if isinstance(c, tuple) else RANKS.index(str(c)[0]) for c in board) if False else None
    br = sorted((RANKS.index(c[0]) for c in BOARD_RAW), reverse=True)
    hr = [RANKS.index(c[0]) for c in HOLE_RAW]
    pair_rank = None
    for r in hr:
        if r in br or hr.count(r) == 2: pair_rank = r if pair_rank is None else max(pair_rank, r)
    if pair_rank is None: return "pair_low"          # pair on board only
    return "pair_top" if pair_rank >= br[0] else "pair_low"

def size_bucket(action, add, pot_before):
    if action == "call": return "call"
    if action == "raise": return "raise"
    f = add / pot_before if pot_before else 1
    if action == "all-in" and f > 1: return "big"
    return "small" if f < 0.5 else "med" if f <= 1.0 else "big"

def walk(h):
    """yield (street, seat, bucket, live_seats_after) for postflop voluntary actions"""
    pot = 0; contrib = collections.Counter(); high = 0; st = None; folded = set()
    for e in sorted(h.get("recentEvents") or [], key=lambda e: e.get("sequence", 0)):
        s = e.get("street"); sm = e.get("summary") or {}; seat = sm.get("seatNumber")
        if s != st and s in ORDER:
            st = s; contrib = collections.Counter(); high = 0
        if e.get("type") == "BlindPosted" and sm.get("amount"):
            contrib[seat] += sm["amount"]; pot += sm["amount"]; high = max(high, contrib[seat]); continue
        if e.get("type") not in ("ActionTaken", "TimeoutAction"): continue
        a = sm.get("action"); to = sm.get("toAmount"); add = 0
        if a == "fold": folded.add(seat); continue
        if a in ("bet", "raise", "all-in") and to: add = max(0, to - contrib[seat])
        elif a == "call": add = sm.get("amount") or max(0, high - contrib[seat])
        before = pot; contrib[seat] += add; pot += add; high = max(high, contrib[seat])
        if st in BL and a in ("bet", "raise", "all-in", "call"):
            yield st, seat, size_bucket(a, add, before), set(folded)

def samples(exclude=()):
    for l in open("hand_archive.jsonl", encoding="utf-8"):
        h = json.loads(l)
        if h["id"][:8] in exclude: continue
        board_all = h.get("boardCards") or []
        hc = {s["seatNumber"]: s["holeCards"] for s in h.get("seats", []) if s.get("holeCards")}
        for st, seat, b, folded in walk(h):
            if len(board_all) < BL[st] or seat not in hc: continue
            braw = board_all[:BL[st]]; board = [P(c) for c in braw]
            o = S._best_hand_rank([P(c) for c in hc[seat]] + board)
            for other, cards in hc.items():
                if other == seat or other in folded: continue
                global BOARD_RAW, HOLE_RAW; BOARD_RAW, HOLE_RAW = braw, cards
                hh = [P(c) for c in cards]
                yield (st, b, h_class(hh, board)), o > S._best_hand_rank(hh + board)

def fit(exclude=()):
    n = collections.Counter(); k = collections.Counter()
    for key, ahead in samples(exclude):
        n[key] += 1; k[key] += ahead
    return {"|".join(key): [k[key], n[key]] for key in n}

if __name__ == "__main__":
    ex = set(open(sys.argv[1]).read().split()) if len(sys.argv) > 1 else set()
    t = fit(ex)
    json.dump(t, open("analysis/beat_rates.json", "w"), indent=0, sort_keys=True)
    for key in sorted(t):
        a, m = t[key]
        if m >= 30: print(f"{key:28} n={m:5}  opp ahead {a/m:.2f}")
