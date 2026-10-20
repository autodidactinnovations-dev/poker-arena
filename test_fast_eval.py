"""fast_eval must match strategy._best_hand_rank exactly.   py test_fast_eval.py"""
import random
import strategy as S
import fast_eval as F

orig = S._best_hand_rank
deck = [S._parse_card(r + s) for r in "23456789TJQKA" for s in "cdhs"]
rng = random.Random(42)
for i in range(100000):
    k = rng.choice([6, 7, 7, 7])
    cards = rng.sample(deck, k)
    a, b = orig(cards), F.best_hand_rank(cards)
    assert a == b, (cards, a, b)
print("ALL PASS - 100,000 random hands identical")
