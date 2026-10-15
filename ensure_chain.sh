#!/usr/bin/env bash
# Hourly guard: restart the chain ONLY if nothing is running.
#
# WHY BASH AND NOT POWERSHELL. The first version of this guard used
# PowerShell's Start-Process to spawn bash. Detection worked perfectly - on
# 2026-10-08 06:09 it correctly logged "DEAD - relaunching chain at v182" - but
# the launch silently did nothing: no process, and no run log, which is
# conclusive because run_chain.sh writes its log as the first act of a run.
# Probed afterwards with a harmless surrogate: Start-Process to bash.exe with
# both `-lc` and `-c` produced ZERO processes and ZERO file writes, while the
# same bash invoked synchronously worked fine. So Start-Process cannot spawn a
# surviving child here. `nohup ... &` from inside bash can, and is the pattern
# every successful chain launch has used.
#
# That failure is also a Principle 17 case: the old guard "succeeded" (exit 0,
# a log line claiming a relaunch) while doing nothing. This version verifies
# the POST-CONDITION - a new run log must appear - and says so if it does not.
#
# THE ONE THING THIS MUST NEVER DO is create a second instance; the arena
# enforces a 1-table concurrency limit. Guards: count python.exe (the real
# interpreter, not the py.exe shim), count ROOT run_chain shells only (a
# run_chain bash whose parent is another is the same logical chain), require
# zero on TWO readings 25s apart, and re-check once more immediately before
# launching.
cd /c/Users/billy/poker-arena || exit 1
LOG=ensure_chain.log

# EXPLICIT PATH. Task Scheduler runs this with a near-empty environment, so the
# first task-invoked run found neither `date` nor `powershell`: it logged an
# empty "[]" timestamp and "UNREADABLE process counts". The fail-safe held (it
# refused to act rather than guessing), but the guard was inert. An
# interactively-tested script is NOT a scheduler-tested script - the
# environment is the thing that differs.
export PATH="/usr/bin:/bin:/c/Windows/System32:/c/Windows/System32/WindowsPowerShell/v1.0:$PATH"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

ps_count() {   # $1 = regex over the full command line, $2 = process name
  powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='$2'\" | Where-Object { \$_.CommandLine -match '$1' }).Count" 2>/dev/null | tr -d '\r' | head -1
}

count_agents() { ps_count 'live_agent' 'python.exe'; }

count_sup_roots() {
  powershell -NoProfile -Command "\$a=@(Get-CimInstance Win32_Process -Filter \"Name='bash.exe'\" | Where-Object { \$_.CommandLine -match 'run_chain\\.sh\"?\\s*\$' }); \$ids=\$a.ProcessId; @(\$a | Where-Object { \$ids -notcontains \$_.ParentProcessId }).Count" 2>/dev/null | tr -d '\r' | head -1
}

# END-ANCHORED on purpose. Unanchored, this matched any bash command line that
# merely CONTAINED "watchdog.sh" - including the shell running this guard, and
# any shell whose arguments mention it - and reported 3 watchdogs when exactly
# 1 was running. Over-counting here would wrongly conclude a watchdog already
# exists and skip starting one.
# Single backslashes: this string is passed through single quotes straight into
# PowerShell's -match, so '\\.' would mean a LITERAL backslash and match
# nothing (it reported 0 while 1 watchdog was running). Verified against a
# known-true count of exactly 1 rather than trusting the number.
count_watchdogs() { ps_count 'watchdog\.sh"?\s*$' 'bash.exe'; }

# UNKNOWN is not HEALTHY. The watchdog's num() defaulted an unreadable count to
# 1 and therefore logged "ok: 1 agent" through a window with zero agents
# running. Here an unreadable count returns "" and the caller refuses to act -
# never restarting is the safe failure for a script whose only danger is
# starting a second instance.
numeric() { case "$1" in ''|*[!0-9]*) echo "" ;; *) echo "$1" ;; esac; }

# A ZERO MUST BE CONFIRMED BEFORE IT IS BELIEVED.
# The WMI count returns a spurious 0 while the process is demonstrably alive -
# seen on 2026-10-10 (twice, logged "agents=0" with a 424s-fresh run log) and
# again on 2026-10-15 (logged "agents=0" with ground truth agents=1). A false
# zero used to be harmless because the log-age test caught it, but now that a
# zero can lead to KILLING a supervisor, believing one reading is unsafe: a
# spurious zero plus a stale-ish log would tear down a healthy chain.
# Non-zero needs no confirmation - only the zero is the dangerous direction.
confirmed_count() {   # $1 = counter function name
  local v
  v=$(numeric "$($1)")
  [ "$v" != "0" ] && { echo "$v"; return; }
  sleep 3
  v=$(numeric "$($1)")
  [ "$v" != "0" ] && { echo "$v"; return; }
  sleep 3
  numeric "$($1)"
}

next_run() {
  local max=0 n
  for f in live_round_v*_chain.log; do
    n=$(echo "$f" | sed -n 's/^live_round_v\([0-9]\+\)_chain\.log$/\1/p')
    [ -n "$n" ] && [ "$n" -gt "$max" ] && max=$n
  done
  echo $((max + 1))
}

newest_log() { ls -t live_round_v*_chain.log 2>/dev/null | head -1; }

log_age() {   # seconds since the newest run log was written; huge if none
  local f age
  f=$(newest_log)
  [ -z "$f" ] && { echo 999999; return; }
  echo $(( $(date +%s) - $(stat -c %Y "$f") ))
}

kill_chain() {   # every agent and supervisor, by PID
  powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='py.exe'\" | Where-Object { \$_.CommandLine -match 'live_agent' }) | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1
  powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='bash.exe'\" | Where-Object { \$_.CommandLine -match 'run_chain\.sh\"?\s*\$' }) | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1
  sleep 5
}

# A DEAD WATCHDOG WAS NEVER REPLACED. The guard only ever started a watchdog as
# part of relaunching the chain, so when the watchdog died on its own the chain
# kept running completely unmonitored. That is exactly what happened overnight
# on 2026-10-15: the watchdog died at 00:19, and because its staleness rule is
# the ONLY check that catches a wedged supervisor, nothing noticed the chain had
# stopped producing runs for 8h21m. The watchdog needs a supervisor of its own.
ensure_watchdog() {
  local w
  w=$(numeric "$(count_watchdogs)")
  if [ "$w" = "0" ]; then
    log "watchdog missing - starting one"
    INTERVAL=300 nohup bash watchdog.sh > /dev/null 2>&1 &
    disown
  fi
}

A=$(confirmed_count count_agents); S=$(confirmed_count count_sup_roots)
if [ -z "$A" ] || [ -z "$S" ]; then
  log "UNREADABLE process counts (agents='$A' sup='$S') - refusing to act"
  exit 0
fi

# The watchdog is restarted independently of everything below: a missing
# watchdog is its own fault, not a symptom of a dead chain.
ensure_watchdog

# AGENT ALIVE IS NOT THE SAME AS CHAIN WORKING. Checked before the healthy
# exit because "an agent process exists" was the condition that hid an 8h21m
# outage on 2026-10-15: v229's supervisor hit a `fork: Resource temporarily
# unavailable` at 00:19 and never started another run, while its agent process
# lingered. Every guard check read agents=1 and reported healthy. Process
# liveness says a pid exists; only the run log says work is happening.
#
# 1800s, not the 900s used below: with an agent alive a thin seating run can
# legitimately be quiet for a while (observed runs of 27-42 decisions over 4h,
# plus an archive write every ~5 min), so this matches the watchdog's own
# staleness rule rather than the tighter between-runs threshold.
WEDGE_SECS=${WEDGE_SECS:-1800}
if [ "$A" -gt 0 ]; then
  AGE=$(log_age)
  if [ "$AGE" -lt "$WEDGE_SECS" ]; then
    # Quiet when healthy so the file stays readable over weeks; a gap in the
    # timestamps is itself diagnostic.
    [ "$(( 10#$(date +%H) % 6 ))" -eq 0 ] && log "ok: agents=$A sup_roots=$S watchdogs=$(count_watchdogs)"
    exit 0
  fi
  sleep 25
  AGE2=$(log_age)
  if [ "$AGE2" -lt "$WEDGE_SECS" ]; then
    log "transient: log refreshed on second reading (age ${AGE2}s) - not acting"
    exit 0
  fi
  log "WEDGED - agents=$A sup_roots=$S but $(newest_log) age ${AGE2}s; killing chain to relaunch"
  kill_chain
  A=$(confirmed_count count_agents); S=$(confirmed_count count_sup_roots)
  if [ "${A:-1}" -gt 0 ] || [ "${S:-1}" -gt 0 ]; then
    log "could not clear wedged chain (agents=$A sup=$S) - leaving for a human"
    exit 0
  fi
fi

# NO AGENT, BUT A SUPERVISOR EXISTS. The first version treated "a supervisor
# exists" as "the chain is working" and stood down. On 2026-10-08 v187's agent
# aborted at 09:12 and its supervisor then hung WITHOUT starting another run;
# the guard saw sup_roots=1 and did nothing for 1h37m, including when the
# waiter invoked it at 10:47 after the network came back. A human had to kill
# it. A supervisor that is not producing runs is not a working chain.
#
# Threshold is deliberately generous: between runs the chain sleeps only 20s,
# and even a run failing every join writes a fresh log every ~2-4 minutes. A
# run log untouched for 15 minutes with no agent alive is unambiguous.
STALL_SECS=${STALL_SECS:-900}
if [ "$S" -gt 0 ]; then
  NEWEST=$(ls -t live_round_v*_chain.log 2>/dev/null | head -1)
  AGE=999999
  [ -n "$NEWEST" ] && AGE=$(( $(date +%s) - $(stat -c %Y "$NEWEST") ))
  if [ "$AGE" -lt "$STALL_SECS" ]; then
    log "between runs: agents=0 sup_roots=$S, ${NEWEST} age ${AGE}s - not acting"
    exit 0
  fi
  sleep 25
  if [ "$(confirmed_count count_agents)" != "0" ]; then
    log "transient: agent appeared on second reading - not acting"
    exit 0
  fi
  log "STALLED - sup_roots=$S but no agent and ${NEWEST} age ${AGE}s; killing hung supervisor(s)"
  powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='bash.exe'\" | Where-Object { \$_.CommandLine -match 'run_chain\.sh\"?\s*\$' }) | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1
  sleep 5
  if [ "$(confirmed_count count_sup_roots)" != "0" ]; then
    log "could not clear hung supervisor(s) - leaving for a human"
    exit 0
  fi
  S=0
fi

sleep 25     # run_chain.sh sleeps 20s between runs; one sample inside that
             # window is indistinguishable from a dead chain.
A2=$(numeric "$(count_agents)"); S2=$(numeric "$(count_sup_roots)")
if [ -z "$A2" ] || [ -z "$S2" ]; then
  log "UNREADABLE on second reading - refusing to act"
  exit 0
fi
if [ "$A2" -gt 0 ] || [ "$S2" -gt 0 ]; then
  log "transient: zero then agents=$A2 sup_roots=$S2 - not acting"
  exit 0
fi

RUN=$(next_run); END=$((RUN + 200))
A3=$(confirmed_count count_agents); S3=$(confirmed_count count_sup_roots)
if [ "${A3:-1}" -gt 0 ] || [ "${S3:-1}" -gt 0 ]; then
  log "race averted: something started just before launch - not acting"
  exit 0
fi

# DON'T RELAUNCH INTO A DEAD NETWORK. On 2026-10-08 the connection dropped and
# v183-v187 each started, failed every join, and exited with zero decisions -
# five wasted run slots that also drove the chain to its 3-strike terminal
# condition. If the arena is unreachable there is nothing to play; the hourly
# cadence retries, so skipping costs at most an hour and wastes nothing.
API=$(py - <<'PY' 2>/dev/null
import importlib.util, sys
spec = importlib.util.spec_from_file_location("la", "live_agent.py")
m = importlib.util.module_from_spec(spec); sys.argv = ["la"]
try: spec.loader.exec_module(m)
except SystemExit: pass
try:
    s, _ = m.call("GET", "/texas/rebuy-status", query={"competitionId": m.COMPETITION_ID})
    print(s)
except Exception:
    print(-1)
PY
)
if [ "$API" != "200" ]; then
  log "arena unreachable (API=${API:-none}) - not relaunching; will retry next hour"
  exit 0
fi

log "DEAD - relaunching chain at v${RUN} (END=${END})"
START=$RUN END=$END nohup bash run_chain.sh >> chain_supervisor.log 2>&1 &
disown
sleep 12
if [ "$(numeric "$(count_watchdogs)")" = "0" ]; then
  INTERVAL=300 nohup bash watchdog.sh > /dev/null 2>&1 &
  disown
fi
sleep 15

# VERIFY THE POST-CONDITION, not the exit status. run_chain.sh writes the run
# log as its first act, so the log's existence is proof the chain actually
# started - which is exactly what the previous version never checked.
if [ -f "live_round_v${RUN}_chain.log" ]; then
  log "relaunch OK: live_round_v${RUN}_chain.log exists, agents=$(count_agents) sup_roots=$(count_sup_roots) watchdogs=$(count_watchdogs)"
else
  log "RELAUNCH FAILED: no live_round_v${RUN}_chain.log after 27s - chain did NOT start"
fi
