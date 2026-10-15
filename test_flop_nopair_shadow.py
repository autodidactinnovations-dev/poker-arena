import importlib.util, sys
spec = importlib.util.spec_from_file_location("la", "live_agent.py")
la = importlib.util.module_from_spec(spec); spec.loader.exec_module(la)

def test_scope():
    assert la.flop_nopair_verdict("Turn", 0, .4, .3, 0, ["Ah","Kd"], ["2c","7s","9d","Jc"]) is None
    assert la.flop_nopair_verdict("Flop", 1, .4, .3, 0, ["Ah","Kd"], ["2c","7s","9d"]) is None

def test_air_folds():
    v = la.flop_nopair_verdict("Flop", 0, .40, .25, .02, ["Ah","Kd"], ["2c","7s","9h"])
    assert v["would"] == "fold" and v["draw"] is None

def test_flush_draw_calls():
    v = la.flop_nopair_verdict("Flop", 0, .35, .30, .02, ["Ah","Kh"], ["2h","7h","9c"])
    assert v["draw"] == "flush_draw" and v["would"] == "call"

def test_oesd_calls():
    v = la.flop_nopair_verdict("Flop", 0, .32, .30, .02, ["8h","9d"], ["Tc","Js","2h"])
    assert v["draw"] == "oesd" and v["would"] == "call"

def test_gutshot_not_oesd():
    assert la.strong_draw(["8h","9d"], ["Jc","Qs","2h"]) is None

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
