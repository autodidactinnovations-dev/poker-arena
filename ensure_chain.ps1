# Hourly guard: restart the S19 chain ONLY if nothing is running.
#
# WHY THIS EXISTS. Nothing in the agent's own stack survives a process kill.
# On 2026-10-07 21:37 the agent, supervisor AND watchdog were killed in one
# event, so the watchdog could not alert; 20 minutes were lost. Worse, on
# 2026-09-18 the chain's terminal condition stopped it correctly on a DNS
# outage and then NOTHING restarted it for three weeks - roughly 120 lost runs,
# far more costly than any strategy change. Rank is cumulative chips and the
# leaders have ~10,000 hands to our ~3,900, so idle time is the most expensive
# thing we do.
#
# THE ONE THING THIS MUST NEVER DO is create a second instance: the arena
# enforces a 1-table concurrency limit. Guards, in order:
#   1. Counts python.exe running live_agent (the REAL interpreter - py.exe is
#      only a launcher shim, and an IMAGENAME check on one misses the other).
#   2. Counts ROOT run_chain.sh shells only - a run_chain bash whose parent is
#      another run_chain bash is the same logical chain, not a second one.
#   3. Requires BOTH counts to be zero on TWO readings 20s apart, then checks
#      once more immediately before launching.
# Any nonzero count at any point means do nothing and exit.
[CmdletBinding()]
param([switch]$DryRun)

$Root = "C:\Users\billy\poker-arena"
$Log  = Join-Path $Root "ensure_chain.log"

function Write-Log($msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $Log -Value $line -Encoding utf8
  Write-Output $line
}

function Count-Agents {
  @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -match 'live_agent' }).Count
}

function Count-SupervisorRoots {
  $all = @(Get-CimInstance Win32_Process -Filter "Name='bash.exe'" |
             Where-Object { $_.CommandLine -match 'run_chain\.sh"?\s*$' })
  $ids = $all.ProcessId
  @($all | Where-Object { $ids -notcontains $_.ParentProcessId }).Count
}

function Count-Watchdogs {
  @(Get-CimInstance Win32_Process -Filter "Name='bash.exe'" |
      Where-Object { $_.CommandLine -match 'watchdog\.sh"?\s*$' }).Count
}

function Next-RunNumber {
  # Highest existing run log + 1. Never reuse a number: run_chain.sh writes
  # with '>' and would TRUNCATE the log of a run already in progress (that is
  # how 40 minutes of v122's decisions were destroyed on 2026-09-11).
  $max = 0
  Get-ChildItem -Path $Root -Filter "live_round_v*_chain.log" -ErrorAction SilentlyContinue |
    ForEach-Object {
      if ($_.Name -match 'live_round_v(\d+)_chain\.log') {
        $n = [int]$Matches[1]
        if ($n -gt $max) { $max = $n }
      }
    }
  $max + 1
}

$a = Count-Agents; $s = Count-SupervisorRoots; $w = Count-Watchdogs

if ($DryRun) {
  Write-Log ("DRYRUN: agents={0} supervisorRoots={1} watchdogs={2} nextRun=v{3} -> would {4}" -f `
             $a, $s, $w, (Next-RunNumber), $(if ($a -eq 0 -and $s -eq 0) { "RELAUNCH" } else { "do nothing" }))
  exit 0
}

if ($a -gt 0 -or $s -gt 0) {
  # Healthy, or mid-handoff between runs. Logged at low volume so the file
  # stays readable over weeks; a gap in timestamps is itself diagnostic.
  if ((Get-Date).Hour % 6 -eq 0) {
    Write-Log ("ok: agents={0} supervisorRoots={1} watchdogs={2}" -f $a, $s, $w)
  }
  exit 0
}

# Both zero. Confirm before acting - run_chain.sh sleeps 20s between runs, and
# a single sample taken inside that window looks identical to a dead chain.
Start-Sleep -Seconds 20
$a2 = Count-Agents; $s2 = Count-SupervisorRoots
if ($a2 -gt 0 -or $s2 -gt 0) {
  Write-Log ("transient: zero on first reading, agents={0} supervisorRoots={1} on second - not acting" -f $a2, $s2)
  exit 0
}

$run = Next-RunNumber
$end = $run + 200

# Final check immediately before launch, as close to the action as possible.
if ((Count-Agents) -gt 0 -or (Count-SupervisorRoots) -gt 0) {
  Write-Log "race averted: something started between the second reading and launch - not acting"
  exit 0
}

Write-Log ("DEAD - relaunching chain at v{0} (END={1})" -f $run, $end)
$bash = "C:\Program Files\Git\usr\bin\bash.exe"
Start-Process -FilePath $bash `
  -ArgumentList @("-lc", "cd /c/Users/billy/poker-arena && START=$run END=$end nohup bash run_chain.sh >> chain_supervisor.log 2>&1") `
  -WorkingDirectory $Root -WindowStyle Hidden
Start-Sleep -Seconds 12
if ((Count-Watchdogs) -eq 0) {
  Start-Process -FilePath $bash `
    -ArgumentList @("-lc", "cd /c/Users/billy/poker-arena && INTERVAL=300 nohup bash watchdog.sh > /dev/null 2>&1") `
    -WorkingDirectory $Root -WindowStyle Hidden
}
Start-Sleep -Seconds 8
Write-Log ("post-launch: agents={0} supervisorRoots={1} watchdogs={2}" -f `
           (Count-Agents), (Count-SupervisorRoots), (Count-Watchdogs))
