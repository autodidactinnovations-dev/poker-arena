"""Drop-in faster replacement for strategy._best_hand_rank (6-7 cards).

Returns EXACTLY the same tuples as the original (verified by
test_fast_eval.py on random hands), so decisions are unchanged - it only
makes the equity simulation run faster. Used by sim.py; strategy.py itself is
not modified.
"""
import strategy as S

_orig = S._best_hand_rank


def best_hand_rank(cards):
    if len(cards) <= 5:
        return S._hand_rank_5(cards)
    cnt = {}
    suits = {}
    for v, s in cards:
        cnt[v] = cnt.get(v, 0) + 1
        suits.setdefault(s, []).append(v)
    flush = None
    for s, vs in suits.items():
        if len(vs) >= 5:
            flush = sorted(vs, reverse=True)
            break
    if flush:
        h = _straight_high(set(flush))
        if h:
            return (8, h)
    quads = [v for v, c in cnt.items() if c == 4]
    trips = sorted((v for v, c in cnt.items() if c == 3), reverse=True)
    pairs = sorted((v for v, c in cnt.items() if c == 2), reverse=True)
    if quads:
        q = quads[0]
        return (7, (q, max(v for v in cnt if v != q)))
    if trips and (len(trips) > 1 or pairs):
        t = trips[0]
        p = max(trips[1:] + pairs)
        return (6, (t, p))
    if flush:
        return (5, tuple(flush[:5]))
    h = _straight_high(set(cnt))
    if h:
        return (4, h)
    if trips:
        t = trips[0]
        k = sorted((v for v in cnt if v != t), reverse=True)[:2]
        return (3, (t, *k))
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        return (2, (p1, p2, max(v for v in cnt if v not in (p1, p2))))
    if pairs:
        p = pairs[0]
        k = sorted((v for v in cnt if v != p), reverse=True)[:3]
        return (1, (p, *k))
    return (0, tuple(sorted(cnt, reverse=True)[:5]))


def _straight_high(vals):
    for hi in range(14, 5, -1):
        if all(v in vals for v in range(hi - 4, hi + 1)):
            return hi
    if all(v in vals for v in (14, 5, 4, 3, 2)):
        return 5
    return None


def install():
    S._best_hand_rank = best_hand_rank
