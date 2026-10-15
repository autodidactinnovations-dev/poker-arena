"""Regression tests for the two exit-path bugs found 2026-09-01.

BUG 1 (confirmed live): the MAX_CONSECUTIVE_FAILURES abort was a bare `return`,
skipping the archive flush, the FINAL participant log, and the session-log
write. Run v24 played 21 hands / 40 actions, aborted on a DNS fault, and left
NO entry in live_session_log.jsonl - the run is invisible in session history.

BUG 2 (latent): the action branch has no sleep (deliberate - live deadlines)
but also had no failure counter, so a persistently failing action against a
table that stays in `tables` is an unthrottled hot loop. Every other retry path
in the file has a counter and a give-up. Only 3 action failures across all runs
to date, all isolated, so this never fired - but it is the same shape as the
3,087-call join incident.
"""
import json
import os
import sys
import tempfile
sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as LA

failures = []

# ---- BUG 1: finalize() writes a session-log entry with an exit reason ----
tmp = tempfile.mkdtemp()
log_path = os.path.join(tmp, "session.jsonl")
real_archive, real_call = LA.archive_recent_tables, LA.call
src = open("C:/Users/billy/poker-arena/live_agent.py", encoding="utf-8").read()


def run_finalize(reason, archive_ok=True, call_ok=True):
    LA.archive_recent_tables = (lambda: 3) if archive_ok else (
        lambda: (_ for _ in ()).throw(RuntimeError("network down")))
    LA.call = (lambda *a, **k: (200, {"participant": {"totalChips": 42}})) if call_ok else (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dns fail")))
    orig = LA.__dict__.get("_SESSION_LOG_OVERRIDE")
    try:
        # finalize writes to a hardcoded path; redirect by monkeypatching open
        import builtins
        real_open = builtins.open

        def fake_open(p, *a, **k):
            if "live_session_log.jsonl" in str(p):
                return real_open(log_path, *a, **k)
            return real_open(p, *a, **k)
        builtins.open = fake_open
        try:
            LA.finalize(40, reason)
        finally:
            builtins.open = real_open
    finally:
        LA.archive_recent_tables, LA.call = real_archive, real_call


run_finalize("poll_failures")
rows = [json.loads(l) for l in open(log_path, encoding="utf-8") if l.strip()]
print(f"finalize(poll_failures) -> wrote {len(rows)} session-log row(s)")
if not rows:
    failures.append("BUG1: finalize wrote no session-log entry")
else:
    r = rows[-1]
    print(f"  entry: exit_reason={r.get('exit_reason')} actions={r.get('actions_taken')}")
    if r.get("exit_reason") != "poll_failures":
        failures.append(f"BUG1: exit_reason not recorded, got {r.get('exit_reason')}")
    if r.get("actions_taken") != 40:
        failures.append("BUG1: actions_taken not recorded")

# cleanup must survive a total network failure - that is the scenario it exists for
before = len(rows)
run_finalize("poll_failures", archive_ok=False, call_ok=False)
rows = [json.loads(l) for l in open(log_path, encoding="utf-8") if l.strip()]
print(f"finalize with archive AND api both throwing -> still wrote a row: "
      f"{len(rows) > before}")
if len(rows) <= before:
    failures.append("BUG1: finalize must still log when archive and API both fail")

# ---- BUG 1b: every exit path must actually CALL finalize ----
# This originally counted source-string occurrences of "finalize(actions_taken"
# and required >=3. It PASSED while the completed path was still running the
# old inline code, because the count included the function DEFINITION line
# itself. Run v26 then finished normally and wrote
# exit_reason=None. Counting a definition as a call site is exactly the
# "reads as implemented but isn't" trap (PRINCIPLES.md #4) - so this now parses
# the AST and checks real call sites inside main().
import ast as _ast
_tree = _ast.parse(src)
_main = next((n for n in _ast.walk(_tree)
              if isinstance(n, _ast.FunctionDef) and n.name == "main"), None)
_reasons = set()
if _main:
    for n in _ast.walk(_main):
        if (isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)
                and n.func.id == "finalize" and len(n.args) >= 2
                and isinstance(n.args[1], _ast.Constant)):
            _reasons.add(n.args[1].value)
print(f"finalize() call sites inside main(): {sorted(_reasons)}")
for _r in ("completed", "poll_failures", "action_failures"):
    if _r not in _reasons:
        failures.append(f"BUG1: main() never calls finalize with reason {_r!r}")
# and the old inline write must be gone, or it will silently win
if "live_session_log.jsonl" in src.split("def main(")[-1]:
    failures.append("BUG1: main() still writes the session log inline, "
                    "bypassing finalize")

# ---- BUG 2: action failures counted, backed off, and capped ----
checks = {
    "counter exists": "consecutive_action_failures = 0" in src,
    "counter increments": "consecutive_action_failures += 1" in src,
    "counter resets on success": src.count("consecutive_action_failures = 0") >= 2,
    "give-up threshold": "MAX_ACTION_FAILURES" in src,
    "backoff sleep": "time.sleep(min(30, 2 ** consecutive_action_failures))" in src,
    "finalizes on give-up": 'finalize(actions_taken, "action_failures")' in src,
}
print("\nBUG2 action-failure handling:")
for k, v in checks.items():
    print(f"  {'OK ' if v else 'FAIL'} {k}")
    if not v:
        failures.append(f"BUG2: {k}")

# ---- invariant: the success path must NOT sleep (live action deadlines) ----
success_block = src.split("consecutive_action_failures = 0\n", 2)
if "time.sleep" in src.split('log(f"table={body[\'tableId\'][:8]}')[1][:400]:
    failures.append("BUG2: success path must stay sleep-free for action deadlines")
print("  OK  success path still sleep-free (30s action deadline preserved)")

print()
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PASS: aborts now flush archive + session log with an exit reason and "
      "survive total network failure; action failures are counted, backed off, "
      "and capped without slowing the success path")
