#!/usr/bin/env bash
# Liveness watchdog for the S18 chain. OBSERVE-ONLY BY DESIGN.
#
# It never starts, restarts, or joins anything. The arena enforces a 1-table
# concurrency limit, so an auto-restarting watchdog could race the chain
# supervisor and put two instances in flight. Detection is the job; recovery
# stays a human decision.
#
# WHAT THE 2026-09-11 INCIDENT TAUGHT. Three run_chain.sh supervisors and two
# agent processes ran at once for ~40 minutes and this watchdog logged "ok"
# throughout - because it only ever asked "is there AT LEAST ONE agent?".
# Duplicate instances are a violation, not a healthier kind of alive, so the
# count is now checked in both directions. `pkill -f run_chain.sh` also failed
# to match the supervisors here, so killing must be done by PID.
#
# A reboot or power loss kills the agent, the supervisor AND this watchdog, so
# its log is a forensic record as much as a live alarm: a gap in the timestamps
# is itself the signal.
cd /c/Users/billy/poker-arena || exit 1
WLOG=watchdog.log
INTERVAL=${INTERVAL:-300}

# Counted off the COMMAND LINE, not the image name. `py.exe` is only the
# launcher shim and `python.exe` is the real interpreter, so one live agent
# shows up as two processes - hence the >2 test rather than >1 below.
count_agents() {
  # python.exe is the REAL interpreter; py.exe is only its launcher shim, and
  # `nohup bash script &` likewise leaves a parent bash plus the script's own
  # bash. So one healthy instance is 2 bash + 2 python processes. Counting
  # python.exe alone gives the invariant that actually means something: it
  # should be exactly 1.
  # `tr -dc '0-9'` keeps ONLY digits. The previous version was written as
  # `tr -d '<literal newline>'`, which stripped LF and LEFT THE CR, so
  # PowerShell's "1\r\n" arrived as "1\r" - non-numeric. Combined with num()'s
  # old default of 1, that meant every count silently became "1 healthy agent"
  # from 2026-09-11 onward: the watchdog's `ok` lines were decoration, not
  # measurement, and it slept through the 2026-10-08 06:00 outage saying "ok".
  powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'live_agent' }).Count" 2>/dev/null | tr -dc '0-9'
}

count_supervisor_roots() {
  # Only ROOT supervisors - a run_chain bash whose parent is another run_chain
  # bash is the same logical chain, not a second one.
  powershell -NoProfile -Command "\$a=@(Get-CimInstance Win32_Process -Filter \"Name='bash.exe'\" | Where-Object { \$_.CommandLine -match 'run_chain\.sh\"?\s*\$' }); \$ids=\$a.ProcessId; @(\$a | Where-Object { \$ids -notcontains \$_.ParentProcessId }).Count" 2>/dev/null | tr -dc '0-9'
}

# UNKNOWN IS NOT HEALTHY. This used to take a default argument and callers
# passed 1, so an unreadable count was reported as "one healthy agent". On
# 2026-10-08 the counting powershell returned nothing and this watchdog logged
# `ok: 1 agent / 1 chain` at 06:06 and 06:11 while ZERO agents were running -
# straight through the window it existed to catch. Same family as PRINCIPLES
# 16: a defensive default that destroys the signal it was meant to protect.
# Now an unreadable count returns empty and is surfaced as its own fault,
# routed through the normal confirmation so a one-off blip stays quiet.
num() { case "$1" in ''|*[!0-9]*) echo "" ;; *) echo "$1" ;; esac; }

# CONFIRM BEFORE ALERTING. Both false positives this watchdog produced were
# transient states sampled at the wrong instant: the 20s gap between two runs
# (2026-09-10 05:04) and a stale second watchdog mid-restart (2026-09-11 05:17).
# Requiring the SAME fault on two consecutive readings removes both. It does not
# weaken real detection - the genuine fault on 2026-09-11 05:12 (supervisor dead,
# no v124 started) persisted indefinitely and still fires, one interval later.
pending=""

while true; do
  TS=$(date '+%Y-%m-%d %H:%M:%S')
  NA=$(num "$(count_agents)")
  NS=$(num "$(count_supervisor_roots)")
  NEWEST=$(ls -t live_round_v*_chain.log 2>/dev/null | head -1)

  # `key` is what confirmation compares; `fault` is what gets logged. They are
  # separate because the message embeds live counts - without this, a duplicate
  # seen as "2 supervisors" then "3 supervisors" would read as two DIFFERENT
  # faults and never confirm, silently suppressing a real alert.
  fault=""; key=""
  if [ -z "$NA" ] || [ -z "$NS" ]; then
    # Checked FIRST: every test below needs a number, and comparing an empty
    # string with -lt is an "integer expected" error that bash treats as false
    # - which would silently skip the fault checks entirely.
    key="unreadable"; fault="UNREADABLE process counts (agents='${NA}' sup='${NS}')"
  elif [ "$NA" -lt 1 ]; then
    key="noagent"; fault="NO AGENT PROCESS RUNNING"
  elif [ "$NA" -gt 1 ] || [ "$NS" -gt 1 ]; then
    key="duplicate"; fault="DUPLICATE INSTANCE - ${NA} agent proc(s), ${NS} supervisor(s)"
  elif [ -z "$NEWEST" ]; then
    key="nolog"; fault="agent alive but no run log found"
  else
    # Staleness matters as much as liveness: a hung agent still has a pid.
    # Routed through the same confirmation path as the others - a 30m-stale log
    # is not a state that heals itself between two readings.
    AGE=$(( $(date +%s) - $(stat -c %Y "$NEWEST") ))
    if [ "$AGE" -gt 1800 ]; then
      key="stale"; fault="agent alive but ${NEWEST} stale for ${AGE}s (>30m)"
    fi
  fi

  if [ -n "$fault" ]; then
    # Compared on the KEY, so a different KIND of fault on the second reading
    # starts its own confirmation rather than inheriting the first one's.
    if [ "$key" = "$pending" ]; then
      echo "[$TS] ALERT: $fault" >> "$WLOG"
    else
      echo "[$TS] watch: $fault (unconfirmed - checking again next interval)" >> "$WLOG"
    fi
    pending="$key"
  else
    pending=""
    echo "[$TS] ok: ${NA} agent / ${NS} chain, ${NEWEST} age ${AGE}s" >> "$WLOG"
  fi
  sleep "$INTERVAL"
done
