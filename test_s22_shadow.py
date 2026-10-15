import importlib.util, random, time
import strategy as S
P = S._parse_card
spec = importlib.util.spec_from_file_location("la", "live_agent.py")
la = importlib.util.module_from_spec(spec); spec.loader.exec_module(la)

H = [P("Qh"), P("Js")]; B = [P("Qd"), P("7c"), P("2s")]   # top pair, OK kicker

def test_no_actions_matches_baseline():
    random.seed(1)
    r, _ = S.estimate_equity_ranged(H, B, [[]], trials=3000)
    random.seed(1)
    b = S.estimate_equity(H, B, num_opponents=1, trials=3000)
    assert abs(r - b) < 0.04, (r, b)

def test_flop_raise_lowers_equity():
    random.seed(2)
    chk, _ = S.estimate_equity_ranged(H, B, [[("Flop", "check")]], trials=2000)
    rse, acc = S.estimate_equity_ranged(H, B, [[("Flop", "bet"), ("Flop", "raise")]], trials=2000)
    assert rse < chk - 0.15, (chk, rse)
    assert acc and acc > 0.02

def test_unknown_rows_are_neutral():
    assert S._s22_ratio("River", "bet") is None
    assert S._s22_ratio("Flop", "all-in") == S._s22_ratio("Flop", "raise")

def test_fast_enough():
    t = time.time()
    S.estimate_equity_ranged(H, B + [P("9h")], [[("Flop", "bet"), ("Turn", "bet")]] * 2,
                             pending_opponents=2)
    assert time.time() - t < 5.0

def test_actions_parse():
    table = {"recentEvents": [
        {"type": "ActionTaken", "street": "Preflop", "summary": {"seatNumber": 3, "action": "call"}},
        {"type": "ActionTaken", "street": "Flop", "summary": {"seatNumber": 3, "action": "bet"}},
        {"type": "ActionTaken", "street": "Flop", "summary": {"seatNumber": 2, "action": "call"}},
        {"type": "ActionTaken", "street": "Flop", "summary": {"seatNumber": 3, "action": "raise"}}]}
    assert S.opponent_postflop_actions(table, 2) == {3: [("Flop", "bet"), ("Flop", "raise")]}

def test_logger_swallows_errors():
    la.shadow_record_s22(None, {})   # no hand id: silently no-op

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            t = time.time(); f(); print("ok", n, f"{time.time()-t:.2f}s")
