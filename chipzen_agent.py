"""
Run the poker-arena bot on chipzen.ai (External-API remote play).

This is a SEPARATE process from live_agent.py and uses a separate Chipzen
token, so it can run at the same time as the dev.fun bot without fighting
over a seat.

Setup (one time):
    pip install chipzen-bot==0.4.0
    Create chipzen.toml next to this file (it's git-ignored):
        [external_api]
        token  = "cz_extbot_..."
        bot_id = "<your bot uuid from chipzen.ai>"

Run (from bash, same as live_agent.py, so the log stays UTF-8):
    py chipzen_agent.py > chipzen_run.log 2>&1

Each decision is appended to chipzen_decisions.jsonl (git-ignored) so the
same analysis you do on dev.fun hands can be done on Chipzen hands.
Keep the two datasets SEPARATE - different pool, different format (often
heads-up), so per PRINCIPLES.md they must never be pooled.
"""

import asyncio
import json
import sys
import time

from chipzen import Bot, run_external_bot

import chipzen_adapter as A

DECISIONS_PATH = "chipzen_decisions.jsonl"


class PokerArenaBot(Bot):
    def __init__(self):
        super().__init__()
        self.game_config = {}
        self.match_id = "cz"

    def on_match_start(self, match_info):
        cfg = match_info.get("game_config") or (match_info.get("state") or {}).get("game_config") or {}
        self.game_config = cfg
        self.match_id = str(match_info.get("match_id") or "cz")[:12]
        print(f"[match_start] {self.match_id} config={cfg}", flush=True)

    def decide(self, state):
        t0 = time.time()
        action, decision = A.decide_chipzen(state, self.game_config, self.match_id)
        dt = round(time.time() - t0, 3)
        rec = {
            "t": time.strftime("%Y-%m-%dT%H:%M:%S"), "match": self.match_id,
            "hand": state.hand_number, "phase": state.phase,
            "hole": [str(c) for c in state.hole_cards], "board": [str(c) for c in state.board],
            "pot": state.pot, "to_call": state.to_call, "stack": state.your_stack,
            "players": 1 + len(state.opponent_stacks),
            "decision": {k: v for k, v in decision.items() if k != "_debug"},
            "decided_by": (decision.get("_debug") or {}).get("decided_by"),
            "sent": action.to_wire(), "secs": dt,
        }
        try:
            with open(DECISIONS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass
        print(f"[{state.phase}] hole={rec['hole']} board={rec['board']} -> {rec['sent']} ({dt}s)", flush=True)
        return action

    def on_round_result(self, message):
        # Log every hand result; showdown hands reveal the opponent's cards.
        try:
            res = message.get("result") or (message.get("state") or {})
            with open("chipzen_showdowns.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"match": self.match_id, "calldown": A.CALLDOWN,
                                    "round_id": message.get("round_id"), "result": res}) + "\n")
        except Exception:
            pass

    def on_match_end(self, results):
        print(f"[match_end] {self.match_id} {results}", flush=True)


if __name__ == "__main__":
    # Token / bot_id come from chipzen.toml. One instance per match.
    # Default: stop after ONE match (safe smoke test). Pass a number to play
    # more, or 0 for unlimited:  py chipzen_agent.py 5
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    asyncio.run(run_external_bot(PokerArenaBot, max_matches=(n or None)))
