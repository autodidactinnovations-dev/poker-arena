"""One-off: play out the already-active table from a prior aborted round,
then leave and confirm canStopSafely, without calling /texas/join again."""
import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
from live_agent import call, decide_live, log, COMPETITION_ID
import time

actions_taken = 0
consecutive_failures = 0
MAX_CONSECUTIVE_FAILURES = 8
deadline = time.time() + 180

while time.time() < deadline:
    status, resp = call("GET", "/texas/pending-actions", query={"competitionId": COMPETITION_ID})
    if status != 200:
        consecutive_failures += 1
        backoff = min(60, 2 ** consecutive_failures)
        log(f"pending-actions error {status} {resp} (failure #{consecutive_failures}, backing off {backoff}s)")
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            log("giving up on drain after too many failures")
            break
        time.sleep(backoff)
        continue
    consecutive_failures = 0

    tables = resp.get("tables") or []
    active_tables = resp.get("activeTables") or []
    runner = resp.get("runner") or {}

    if tables:
        table = tables[0]
        decision = decide_live(table)
        body = {"tableId": table.get("tableId") or table.get("id"), **decision}
        status, action_resp = call("POST", "/texas/action", body)
        actions_taken += 1
        if status != 200:
            log(f"action FAILED {status} {action_resp} body={body}")
        else:
            log(f"table={body['tableId'][:8]} street={table.get('street')} action={decision['action']} amount={decision.get('amount',0)} -> ok")
        continue

    if not active_tables:
        log("no active tables, leaving")
        status, resp = call("POST", "/texas/leave", {"competitionId": COMPETITION_ID})
        log(f"leave -> {status} {resp}")
        break

    if runner.get("canStopSafely"):
        log("canStopSafely=true already, leaving")
        status, resp = call("POST", "/texas/leave", {"competitionId": COMPETITION_ID})
        log(f"leave -> {status} {resp}")
        break

    time.sleep(2)

status, final = call("GET", "/texas/pending-actions", query={"competitionId": COMPETITION_ID})
log(f"FINAL participant: {final.get('participant')}")
log(f"runner: {final.get('runner')}")
log(f"actions_taken={actions_taken}")
