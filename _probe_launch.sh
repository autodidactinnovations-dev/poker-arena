#!/usr/bin/env bash
# TEMPORARY PROBE. Answers one question: does a `nohup ... & disown` child
# launched from a Task-Scheduler-invoked bash SURVIVE after the task's own
# process exits? If it does not, the guard can detect a dead chain but never
# restart one - which is the exact failure the PowerShell version had.
export PATH="/usr/bin:/bin:/c/Windows/System32:/c/Windows/System32/WindowsPowerShell/v1.0:$PATH"
cd /c/Users/billy/poker-arena || exit 1
echo "[$(date '+%H:%M:%S')] probe: launching detached sleeper" >> _probe_launch.log
nohup bash -c 'sleep 120; echo done >> /c/Users/billy/poker-arena/_probe_launch.log' > /dev/null 2>&1 &
disown
echo "[$(date '+%H:%M:%S')] probe: launched, task exiting now" >> _probe_launch.log
