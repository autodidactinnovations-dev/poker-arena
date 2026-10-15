# SolWorks Poker Agent — Review Principles

These aren't poker tactics. They're the reasoning discipline that's caught
real bugs across past sessions, and that should be applied every time a
run is reviewed, a fix is proposed, or a signal is audited.

## 1. Evidence should accumulate correctly, not reset or decay incorrectly
If a signal is meant to represent "how much has this opponent shown me,"
check that later evidence builds on earlier evidence rather than
replacing it. A per-street or per-poll signal that forgets prior streets
is not tracking what it claims to track. (See: range_strength reset bug,
S16/S17.)

## 2. Distrust a pattern until the mechanism is found
A losing streak or a striking stat is a lead, not a conclusion. Before
crediting or blaming a fix, find the actual code path or decision that
produced the result. Small samples (a few hands, one session) can look
meaningful and still be noise — the pattern is only trustworthy once
it's traced to a mechanism, not just observed twice.

## 3. Check for confounds before crediting a fix
If conditions changed between "before" and "after" (different stack
depth, different opponent pool, different session length), rule out
whether the confound — not the fix — explains the new result. A clean
result after a fix doesn't confirm the fix if something else also
changed.

## 4. Distinguish "reads as implemented" from "actually implemented"
Code that exists, has a plausible name, and even has its own test can
still be dead — unused by any real decision path. Check for a
production caller before trusting that a signal is doing anything.
Dead code that looks alive is worse than an obvious gap, because it
creates false confidence.

## 5. Don't trade one failure mode for its opposite
When fixing an under-caution bug (e.g., a discount too generous to the
villain), verify the fix doesn't overcorrect into over-caution (folding
genuinely strong hands). Check both directions — the fix should move
the middle, not just flip which side it errs on.

## 6. Flag deviations from the brief, don't bury them
If a fix requires deleting code, changing a test's expected value, or
going beyond what was asked, surface that explicitly with reasoning —
never silently absorb a design change into "the fix" without a flag.

## 7. Audit the monitors as suspiciously as the code they watch
A detector, alarm, or dashboard is code too, and it fails the same ways.
A monitor that confidently reports a wrong number is worse than no monitor:
it sends you investigating a non-problem, and it launders a guess into
something that looks measured. Before acting on an alert, confirm the alert
itself is right — especially when its classes are asymmetric (folding a
winner and checking-instead-of-betting a winner are not the same failure and
must not share a label). (See: the false-positive detector reporting
`pot_forfeited=303` on a pot the agent had in fact **won**.)

Three failure modes, all seen in this project, and they are distinct:

1. **The monitor computes the wrong number.** (`pot_forfeited=303` on a won pot.)
2. **The monitor cannot see the case it exists for.** A detector whose evidence
   requirement is never met in the failure it targets reports "nothing found"
   forever, and that silence reads exactly like health. Ask what evidence the
   detector needs and whether the failure it hunts *produces* that evidence.
3. **The monitor never ran.** Verifying that a script's *configuration* is
   correct is not verifying the script *executes*. A grep that confirms a
   version string, a path, or a flag was updated proves only that the edit
   landed — it says nothing about imports, dependencies, or whether the
   process starts. **Run it once and look at the output.**

Silence from a monitor is only evidence of health if you have separately
confirmed the monitor can speak. Until then, "no alerts" means "no alerts",
not "no problems" — and it must be reported with that wording.

**Never run a monitor with its stderr discarded.** The dead-detector incident
was not subtle underneath: the watcher ran `py brake_false_positive.py
2>/dev/null`, so a `ModuleNotFoundError` on every single invocation produced
exactly the same output as a clean run with nothing to report — none. The
suppression was added to keep transient noise out of the event stream and it
silently converted a hard failure into a healthy-looking silence. Route stderr
*into* the alert channel and filter it, rather than to `/dev/null`.

Corollary: prefer **derived** configuration over configuration that must be
updated by hand each run. A hardcoded log path has to be repointed every time
and was caught stale twice; `newest_log()` cannot be.

## 8. When a shared signal is rebuilt, find every consumer
Fixing a signal in one place while a sibling consumer still reads the old,
broken version leaves the bug half-alive and much harder to see — the metric
now looks correct in the logs while a different code path quietly ignores it.
Grep for every reader before calling a signal rebuild done. (See:
`range_strength` rebuilt on cumulative aggression while the velocity brake
still read the per-street `opp_raises` counter — cost 258 chips on
`cmtfd8a3`.)

## 9. Distrust should be scoped to what's actually uncertain
A safeguard built to distrust one kind of number (a modeled estimate)
can end up applying that same distrust to a different kind of number
that merely looks similar (a deterministic fact) — if nothing in the
mechanism distinguishes them. Before applying a skepticism-based
safeguard broadly, check what specifically justified the distrust, and
confirm the safeguard only fires where that justification actually
holds. (See: velocity brake folding a completed ace-high flush — the
brake was built to distrust self-computed *opponent* equity estimates,
but applied the same distrust to our own made hand rank, which is read
off the table, not modeled. `cmtg76xo`, −377.)

## 10. A pattern measured after the fact may not exist when the decision is made
A signal can show a strong, real separation between outcomes — and still be
useless for changing those outcomes, if it depends on information that
isn't available until after the decision point it would need to inform.
Check not just whether a pattern separates results in aggregate, but
whether it's computable at the specific moment a real decision would use
it. A strong retrospective signal and a usable decision-time signal are
different claims. (See: the "board plays" hand-category signal — a
deterministic, 12× separator at showdown, but only computable on a
5-card river board, meaning it cannot address the flop/turn decisions
where the actual chip commitment happens. Distinct from Principle 9:
that's about a mechanism being scoped to the wrong domain; this is about
a mechanism being scoped to the wrong *time*.)

## 11. A rate measured on one population is not a rate for another
A number can be correctly measured and still be wrong to use, if it is
applied to a population it was not measured on. Watch especially for
rates derived from a *self-selected* subset — decisions you chose to
make, hands that happened to survive, a window that only retains recent
items — then used as if they described everything. Before reusing a
measured rate, state out loud what population it came from and what
population you are applying it to; if those differ, the number needs
conditioning, not just a haircut. (See: `P_FOLD_SINGLE`, measured at
0.55 on bets the agent *chose* to make — rare, value-heavy,
board-selected — then applied to every spot where it currently checks.
The result was a model that could never output "check" at all.)

---

## 12. A safeguard another path can route around is not a safeguard
Principle 4 covers a signal that is dead. This is the harder case: the
signal is alive, correct, and tested — and a second code path reaches
the same decision without consulting it. `escalation_bump` and
`_opponent_raises_this_street` were built after the round-51 three-raise
bust and work exactly as designed; the Stage 1 3-bet gate re-raised on
card class alone and never read them, re-running that same bust for
-623 chips. The safeguard's own tests all passed, because the bypass is
invisible from inside the thing being bypassed.

So: when adding a gate that can produce an action some existing
safeguard governs, the question is not "does my gate work" but "what
does my gate now let through that used to be caught". Grep for every
writer of the action, not just the safeguard's own call sites.

Corollary — **two decision paths must not share an attribution label.**
Both 3-bet gates emitted `"premium hand, re-raising for value"`, so the
log could not separate a raise that BYPASSED the threshold from one that
CLEARED it. All 8 sub-threshold firings in v35 read as normal. A label
that cannot distinguish the bug from correct behaviour is not
instrumentation. Split the label when you split the path.

## 13. A test written from a bug report tests the report, not the bug
Asked to audit for "a fallthrough to fold when the intended action
isn't legal", I checked that condition, found the velocity brake
guarded against it, and cleared it. Two days later the brake folded two
pair at 3.7:1 by the same fallthrough — its intended action was legal,
merely unaffordable. One word away from the wording I was given, and
the test I wrote encoded the wording rather than the defect.

Write the assertion as the OUTCOME that must not happen, not the
mechanism you were told about: not "no safeguard folds when the
downgrade is illegal" but "no fold occurs while a strictly cheaper
legal action exists". An outcome assertion catches routes nobody has
thought of yet; a trigger assertion catches only the one already found.

Corollary — when a brief names a specific mechanism, treat it as one
instance, not the specification. The person writing it is describing
the case they saw. Ask what the general form is before scoping the
test to their wording.

## 14. "Provably unavoidable" is scoped to the axis you measured
We proved cmtkwn8t could not be un-braked without also un-braking the
cmtcarsk bust: any pot-odds bar wide enough for one admits the other,
since the fatal call was the CHEAPER of the two. Correct, and it stayed
correct — the cost was recorded as permanently accepted.

It was only ever a statement about pot odds. The two hands differ in
action type (a refused raise vs a refused call), and on that axis they
separate cleanly, recovering the accepted cost for free.

So when an argument concludes a cost is unavoidable, the conclusion
carries an implicit "…by the property I was measuring". Before filing
it as permanent, ask which other properties differ between the cases.
A trade-off that looks forced usually means the discriminator hasn't
been found yet.

## 15. "Best hand" and "won the pot" are different measurements
Conflating them flipped three conclusions in a single session, twice
almost shipping a change:

  - counting hands we FOLDED as losses produced a -0.347 equity
    "over-confidence gap" that did not exist;
  - ranking against every seat with visible cards, including folded
    ones, produced a 12% win rate that was pure artifact;
  - a texture signal reading "0 of 13 hands were best" looked like the
    strongest evidence in the project - four of those 13 won +120 chips,
    because the better hands folded.

All three made the agent look worse than it is. Where the arena records
the outcome (`winners`, `payoutChips`), that is ground truth for money;
reconstructing it from hand ranks answers "who held the best cards",
which is a different question. Fold equity lives in exactly that gap.

Use rank comparison only when the question is genuinely about hand
strength, and never as a proxy for profit.

## 16. Error handling that corrupts the value it was meant to protect
The chain runner counted a run's decisions with

    ELAPSED_LINES=$(grep -c "table=" "$LOG" 2>/dev/null || echo 0)

`grep -c` PRINTS `0` and EXITS 1 when it matches nothing, so on exactly the
case the guard existed for, the `||` fired and appended a second value:
`"0
0"`. The `-lt` test then failed with "integer expected", bash treated
that as false, the 120-second backoff never ran, and the loop consumed 42 run
slots in 100 minutes during an API outage. **The guard written to prevent
hot-looping produced a faster one**, and nothing surfaced until a human looked.

Same family as suppressing a monitor's stderr and as a test that asserts on a
value it mutated itself: the defensive construct did not merely fail to help,
it destroyed the signal. `|| default` is safe only when the command is silent
on failure - `grep -c`, `wc -l` on a missing file, and anything that prints a
partial result before a non-zero exit are not.

Two rules that would have caught it:
  - Validate the shape of a value you are about to compare, don't assume the
    fallback produced a scalar.
  - **Exercise the guard.** This one had never run: no chain run had produced
    zero decisions until the outage. A guard that has never fired is untested
    code on the most important path, and testing it is usually a two-line fake.

## 17. A command that succeeds is not a command that did something
Stopping the chain to ship §19, the instance was killed with

    pkill -f run_chain.sh

It matched nothing. It printed nothing, and the surrounding script carried on.
The three supervisors kept running. Liveness was then "confirmed" with

    tasklist //FI "IMAGENAME eq py.exe"

which does not match `python.exe` - the actual interpreter. So the kill did
nothing, the check looked at the wrong process, and both reported success.
Two agents played the same competition concurrently for 39 minutes, violating
the arena's 1-table limit. One supervisor later relaunched `v122` with a `>`
redirect and **truncated the log of the run already in progress**, destroying
40 minutes of decision data. None of it surfaced until a watcher fired on a
run-end line for a run believed already dead.

`pkill -f` failed to match three times in one session - twice on
`run_chain.sh`, once on `watchdog.sh` - and reported success every time.

Same family as §16's `$(grep -c ... || echo 0)` and as `2>/dev/null` hiding a
`ModuleNotFoundError`: the tool ran, returned cleanly, and did nothing. Exit
status answered "did the command execute?" when the question was "is the target
gone?"

**Verify the post-condition, not the exit status.** After a kill, count the
target and assert zero. After a write, read it back. After a start, confirm the
thing is doing work - not merely that a pid exists.

Two corollaries this incident added:
  - **Count process TREES, not processes.** One healthy instance here is two
    bash plus two python: `nohup bash script &` leaves a parent and the
    script's own shell, and `py.exe` is a launcher shim that spawns
    `python.exe`. A naive count reads one instance as a duplicate - which is
    exactly the false alarm the first fix produced. Distinguish by parentage.
  - **A monitor asking "at least one?" cannot see "too many."** The watchdog
    logged `ok` throughout the 39-minute violation because that was the only
    question it asked. Invariants need both bounds.

## Worked examples

Each principle above came out of a specific incident. Kept concrete so the
rule stays checkable rather than becoming a slogan.

| # | Incident | What it cost / caught |
|---|---|---|
| 1 | `range_strength` fell 0.70 → 0.35 entering the turn on `cmtewk95`, because `opp_raises_this_street` reset and `prior_streets` counted *how many* streets, not *how hard*. The villain's two flop raises were forgotten. | −421 chips. Fixed by `cumulative_aggression`. |
| 2 | Six S16 hands all showed declining equity before a turn/river fold. Tempting to call it variance; tracing it found `opp_raises=0` whenever villains *bet* rather than raised, so the flop discount was always 0. | Real mechanism, not a streak. Confirmed again on S17 before being fixed. |
| 3 | Apparent throughput collapse 0.37 → 0.12 hands/min looked like rate limiting or a connection fault. The real cause: **S16 ended mid-run**; the denominator included two dead hours. True rate was 0.24. | Prevented chasing a non-existent network bug. |
| 4 | `_opponent_continuation_count` had a plausible name, a docstring, and its own passing test — and no production caller after the aggression rebuild. Also FINDINGS.md Leak 4: `aggression` was unpacked and never used in any decision. | 33 lines of false confidence deleted. |
| 5 | Removing the `0.95` equity override was correct for the velocity brake (cost 0 false positives). Applying the *same* reasoning to the level check would have **folded the +207 winner** — the winner sat between two busts at 0.906 equity. | Caught before shipping. No equity threshold separates those hands; level is the wrong discriminator. |
| 6 | The aggression rebuild required deleting a function and inverting a test that asserted `0.7` for a passive caller (now `0.239`). Both were surfaced explicitly rather than absorbed into "the fix." | Reviewer saw the real scope of the change. |
| 7 | The brake false-positive detector paged with `pot_forfeited=303` on a hand the agent **won** (+152, two pair). Its "would have won" test was correct for a fold but wrong for a downgrade, where we stay in and may win anyway. | Fixed into a three-way split: CRITICAL (folded a winner) / MINOR (stayed in, lost) / VALUE-MISSED (won anyway — informational, does not page). |
| 7b | **The monitor never ran.** `arena.py` vanished from the scratchpad; only its Aug-26 `.pyc` survived, so every script doing `from arena import call` — the brake detector and the hourly status ping included — died at `ModuleNotFoundError`. v33's pre-flight check confirmed the detector's *log path string* had been repointed (`grep` count = 1) and stopped there. The script had not been executed once. | The whole of v33 ran unmonitored, and the run's largest error (a ~6000-chip pot forfeited at 19.5:1) went unreported. **Third instance of this shape in one day**: a `sed` repoint that silently matched nothing, a heredoc-escaping fix that never applied, and now a detector that could not import. All three "verified" by inspecting text rather than by running the thing. |
| 7c | **The monitor could not see its own target case.** The brake detector required a completed 5-card board to compare hands. But when the brake folds, *our fold ends the hand* — no river is dealt, and the opponent takes the pot "Uncontested". Both v33 firings returned INDETERMINATE. The detector was structurally blind to the exact failure it existed to catch, and had been since it was written. | Rewritten with a second, independent test that prices the fold at the decision point (pot, to_call, equity, made_category — all already logged), so it works with no showdown. Graded MODEL-FREE (pot odds alone justify the call; no dependence on the equity estimate) vs PRICED (+EV only if the equity estimate is right — a lead, not a verdict, since the cmtcarsk bust prices at +2763 EV and its fold was correct). |
| 9 | The velocity brake folded a **completed ace-high flush** (`cmtg76xo`, jump 0.803 vs cap 0.183). Its premise — "a self-computed equity number is untrustworthy when commitment spikes" — is about *opponent* uncertainty. Our own made hand is read off the board, not modeled. | −377 chips. Fixed by exempting made-hand category ≥ 5 entirely. Trips (cat 3) still braked, correctly — that caught `cmtd2ab0`'s −1000. |
| 9b | The made-hand exemption bar was set at category 5 (flush). Both validating cases — a flush (exempt) and trips (braked) — passed, but **no straight was ever tested**, leaving the boundary inside an untested gap documented as a deliberate choice. A made queen-high straight was then folded on the river at 0.962 equity. | −352 chips. Bar lowered to 4 with the real principle stated: categories ≤3 carry kicker-domination risk, 4+ do not. Two incidents now: an untested gap reads exactly like a decision. |
| 11 | `P_FOLD_SINGLE` was measured at 0.55–0.658 across 172 real bets — a sound measurement on the population *bets we chose to make*. Applied unconditionally to every checking spot, it fired on **95% of checks** and reduced algebraically to `bet when eq < 1.38`, i.e. it could never say "check". Narrowing the domain to a semi-bluff band cut it to 12.8%, but sensitivity then killed it: **71% of firings flip** when the inputs move 0.05 against betting. | Stage 2 parked behind `FOLD_EQUITY_ENABLED = False`. **Second instance of this error the same day** — the status script recomputed a "cumulative" trial net from a rolling 50-hand window, so `settled` went 8 → 5 and net flipped +82 → −75 purely from rolloff. Both are the same shape: a number true for one population, applied or reported as true for another. |
| 10 | "Hole cards add only a kicker" separated showdown outcomes 12× (2% best vs 24%) across 96 archived hands — the sharpest separator found in the project. At actual value bet/raise decisions it was computable on only **11 of 53** (river boards only); the board-only group collapsed to **n=2**. Two of the three losses that motivated it (`cmthhojp` −259, `cmthdl25` −210, 78% of the damage) committed on flop and turn, *before* the signal exists. | Nothing built. The door is closed permanently, not deferred — it is a property of the signal, not of the sample. Also caught an over-generalisation: `cmthcxch` was the one hand of three where the mechanism applied. |
| 8 | `range_strength` was rebuilt on cumulative aggression, but the velocity brake kept reading `opp_raises_this_street` with a flat −0.05 decrement. On `cmtfd8a3` aggression hit 9.5 and the discount correctly reached 0.67, while the brake still passed the fatal call at 0.184 vs a 0.20 cap. | −258 chips. Fixed by scaling the cap through `velocity_cap_for()`. |

## A recurring shape worth naming

Several separate bugs turned out to be the same thing: **an undiscounted number
treated as reliable in a band where it isn't.** Three busts cleared a hard
equity floor by a hair — 0.809, 0.828, 0.954 — and a +207 winner sat at 0.906
*between* them. Whenever a safeguard is gated on a bare `equity < X`, assume
that gate can be cleared by a hair and ask what evidence *other than the equity
number* is available. See DESIGN_BACKLOG.md §3 for the two remaining instances
(`value_thresh` discount asymmetry, `equity > 0.82` bet sizing).

## How to use this file

Consult it the same way FINDINGS.md is consulted:

- **FINDINGS.md** — what has already been found broken, and its current status.
- **DESIGN_BACKLOG.md** — known-but-unfixed issues, at design stage.
- **PRINCIPLES.md** (this file) — how to reason about any of it.

Before reporting a run analysis or proposing a strategy change, check the
reasoning against these. The list is expected to grow as new patterns
show up in review.
