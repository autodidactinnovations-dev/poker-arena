"""Which bot decisions lose chips? Runs sim.py with a hook on every bot decision.

For each FOLD it computes the bot's real equity against the opponents' actual
cards (known in the sim) and charges the EV it gave up when that equity beat
the pot odds. For each CALL it charges the EV lost when equity was below the
pot odds. Totals are grouped by the rule that made the decision (decided_by).

    py sim_diag.py --opps lag --hands 800
"""
import random, sys, collections
import sim, strategy as S

stats = collections.defaultdict(lambda: {"n": 0, "bad": 0, "ev_lost": 0.0})
rng = random.Random(9)


def equity(hole, opp_holes, board_all, board):
    used = set(hole + board + [c for h in opp_holes for c in h])
    deck = [c for c in S.FULL_DECK if c not in used]
    w = 0.0
    for _ in range(150):
        b = board + rng.sample(deck, 5 - len(board))
        me = S._best_hand_rank([S._parse_card(c) for c in hole + b])
        best = max(S._best_hand_rank([S._parse_card(c) for c in h + b]) for h in opp_holes)
        w += 1 if me > best else (0.5 if me == best else 0)
    return w / 150


def hook(dec, act, gs, holes, live_opps, board_all, board):
    if gs.to_call <= 0 or act not in ("fold", "call"):
        return
    by = (dec.get("_debug") or {}).get("decided_by", "?")
    eq = equity([str(c) for c in gs.hole_cards], [holes[s] for s in live_opps], board_all, [str(c) for c in gs.board])
    call = gs.to_call
    ev_call = eq * (gs.pot + call) - call          # chips, vs folding (0)
    k = (gs.phase, act, by)
    st = stats[k]
    st["n"] += 1
    if act == "fold" and ev_call > 0:
        st["bad"] += 1; st["ev_lost"] += ev_call
    if act == "call" and ev_call < 0:
        st["bad"] += 1; st["ev_lost"] += -ev_call


sim.HERO_HOOK = hook
sys.argv = [sys.argv[0]] + sys.argv[1:]
sim.main()
hands = int(next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--hands"), 500))
print("\nEV lost by decision rule (big blinds per 100 hands), worst first:")
rows = sorted(stats.items(), key=lambda kv: -kv[1]["ev_lost"])
for (ph, act, by), st in rows[:12]:
    print(f"  {st['ev_lost'] / sim.BB / hands * 100:7.1f}  {act:4s} {ph:7s} {by:28s} wrong {st['bad']}/{st['n']}")
