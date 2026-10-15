#!/usr/bin/env bash
# Chained agent runs on Playground S18. One instance at a time - the arena
# enforces a 1-table concurrency limit, so these MUST be sequential.
#
# S18 is free with unlimited rebuys, so a bust costs nothing and the agent
# rebuys itself. Each run gets its own log so `=== STACK RESET` splitting keeps
# pre- and post-rebuy samples separate per run.
cd /c/Users/billy/poker-arena || exit 1

DURATION=${DURATION:-14400}   # 4 hours
START=${START:-41}
END=${END:-99}
MAX_DEAD=${MAX_DEAD:-3}       # consecutive zero-decision runs before giving up

dead=0

for i in $(seq "$START" "$END"); do
  LOG="live_round_v${i}_chain.log"
  echo "[chain] $(date '+%H:%M:%S') starting v${i} -> ${LOG}"
  py live_agent.py "$DURATION" > "$LOG" 2>&1
  RC=$?

  # `grep -c` PRINTS 0 and EXITS 1 when there are no matches, so the obvious
  # `$(grep -c ... || echo 0)` yields the two-line string "0\n0". That made the
  # `-lt` test below an "integer expected" error, which bash treats as false -
  # so the backoff never ran and the loop tore through 42 run slots in 100
  # minutes during an API outage. The guard written to prevent hot-looping
  # caused a faster one. Count without relying on grep's exit status.
  ELAPSED_LINES=$(grep -c "table=" "$LOG" 2>/dev/null)
  case "$ELAPSED_LINES" in
    ''|*[!0-9]*) ELAPSED_LINES=0 ;;
  esac
  echo "[chain] $(date '+%H:%M:%S') v${i} exited rc=${RC}, ${ELAPSED_LINES} decisions"

  if [ "$ELAPSED_LINES" -lt 1 ]; then
    dead=$((dead + 1))
    # Terminal condition. Grinding through the whole range on a dead API is
    # not resilience - it consumes every remaining slot and surfaces nothing
    # until a human happens to look. Stop and make the failure loud.
    if [ "$dead" -ge "$MAX_DEAD" ]; then
      echo "[chain] === CHAIN STOPPED: ${dead} consecutive runs produced ZERO decisions."
      echo "[chain] === Last run rc=${RC}. Most likely the API is unreachable or the"
      echo "[chain] === season ended. Check the tail of ${LOG}, then relaunch with"
      echo "[chain] === START=<next> once the cause is fixed."
      exit 1
    fi
    echo "[chain] no decisions (${dead}/${MAX_DEAD}) - backing off 120s"
    sleep 120
  else
    dead=0
    sleep 20
  fi
done
echo "[chain] === CHAIN COMPLETE: reached END=${END}."
