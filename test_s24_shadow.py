import strategy as S

def ev(seq, street, action, seat, to=None, amount=None, typ="ActionTaken"):
    return {"sequence": seq, "type": typ, "street": street,
            "summary": {"action": action, "seatNumber": seat, "toAmount": to, "amount": amount}}

TABLE = {"street": "Flop", "recentEvents": [
    ev(1, "Preflop", None, 1, amount=1, typ="BlindPosted"),
    ev(2, "Preflop", None, 2, amount=2, typ="BlindPosted"),
    ev(3, "Preflop", "call", 1, amount=1),
    ev(4, "Preflop", "check", 2),
    ev(5, "Flop", "check", 1),
    ev(6, "Flop", "bet", 2, to=8)]}   # 8 into a pot of 4 -> big

def test_bucket_and_pot():
    assert S.s24_last_aggression(TABLE, 1) == (2, "big")
    assert S.s24_last_aggression(TABLE, 2) is None   # our own bet never counts

def test_hand_class():
    assert S.s24_hand_class(["Ah", "Kd"], ["Ac", "7s", "2d"]) == "pair_top"
    assert S.s24_hand_class(["9h", "9d"], ["Ac", "7s", "2d"]) == "pair_low"
    assert S.s24_hand_class(["Qh", "Jd"], ["Ac", "7s", "2d"]) == "air"
    assert S.s24_hand_class(["Ah", "7d"], ["Ac", "7s", "2d"]) == "two_pair"

def test_lookup_direction():
    big = S.s24_ahead_prob("Flop", "big", "pair_low")
    small = S.s24_ahead_prob("Flop", "small", "pair_top")
    assert big is not None and small is not None and big < 0.2 < 0.8 < small

def test_missing_row_is_none_or_fallback():
    assert S.s24_ahead_prob("River", "nonsense", "air") is None

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
