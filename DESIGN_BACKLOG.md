# Design backlog — poker agent

> See `PRINCIPLES.md` for the reasoning discipline. Principle 2 (find the
> mechanism) and Principle 3 (rule out confounds) apply to everything here —
> most items are justified by a handful of hands and remain hypotheses.

Design work, not patches. Nothing here is implemented. Each item states what it
would mean concretely, what the evidence says, how it would be tested safely,
and what would make it *not* worth doing.

Current shipped state for reference: velocity brake live and enforcing,
`STACK_RISK_EQUITY_FLOOR = 0.80` intact (deliberately — see §2), downgrade
guard restored, preflop cap left at 0.45 (simulation showed no better value).

---

## 1. Range-conditioned ("kicker-aware") equity

### The problem

`estimate_equity` deals opponents **uniformly random** hole cards. An opponent
who has raised three times does not hold random cards. Equity is therefore most
overstated exactly when being wrong is most expensive.

The shipped `range_strength` only rejects trials where opponents hold *complete
air* (`_hand_rank_5` category 0). That bar is **categorical**, so it cannot see
kicker problems — which is how two of three busts happened:

| hand | we held | board | villain | outcome |
|---|---|---|---|---|
| `cmtb7bys` | `Qc 8s` (top pair, 8 kicker) | `2s Jc Qd 3h 5c` | `Qs Ts` | −83 |
| `cmtd2ab0` | `Ac 5s` (trip aces, 5 kicker) | `Ad Ah 2c Qd 8s` | `Ts As` | −1000 |

Both times: right hand *category*, losing *kicker*. `range_strength` sees "we
have a pair / trips, opponent probably has a pair" and reports ~95%.

### What "kicker-aware" would actually mean

Not rejection sampling. **Exhaustive range construction:**

1. Enumerate all `C(47,2) ≈ 990–1081` opponent hole-card combinations
   consistent with the known board and our hand.
2. Rank each by `_best_hand_rank(combo + board)`.
3. Define the opponent's continuing range as the top **N%** of that list.
4. Compute equity against *that* set, not against all combos.
5. Tighten `N` as observed aggression rises.

This is exact rather than sampled, and needs no kicker special-casing — kickers
fall out of `_best_hand_rank`'s existing tuple comparison for free.

### Feasibility — measured, not estimated

Full enumeration of 990 combos with rank evaluation: **0.054 s**. The current
2000-trial Monte Carlo costs ~0.55 s. **The exact method is ~10× cheaper than
what already runs**, against a 30 s action deadline. Cost is not a constraint.

### What the evidence actually says — including against this proposal

Measured on the three decisive hands:

| hand | vs random | top 30% | top 20% | top 10% |
|---|---|---|---|---|
| **BUST** `cmtd2ab0` (−1000) | 0.956 | 0.854 | **0.780** | 0.561 |
| **BUST** `cmtb7bys` (−83) | 0.828 | 0.428 | **0.141** | 0.000 |
| **WIN** `cmtd6j6v` (+207) | 0.929 | 0.763 | **0.644** | 0.439 |

Read that carefully before building anything:

- `cmtb7bys` is **fixed decisively** — 0.828 → 0.141. That bust does not happen.
- `cmtd2ab0` is **only partly helped** — still 0.780 at top-20%, above most
  continue thresholds. It would likely still have committed.
- The **winner ranks below a bust** at every percentile (0.644 vs 0.780). A
  naive top-N% filter would penalise the +207 hand *more* than the −1000 hand.

**Conclusion: percentile-of-all-combos is the wrong range model.** Ranking by
absolute hand strength is not the same as "hands that would raise three times
on this board." On a paired-ace board a 3-bet range is specifically *ace-heavy*,
not merely "strong" — and trip aces genuinely is ~78% against generic strength,
so the naive model can't see the domination.

### What it would take to do properly

Action-conditioned ranges, not strength percentiles: weight combos by how
plausibly *that* opponent takes *this* betting line on *this* board texture.
Minimum viable version — condition the range on board texture:

- paired board + heavy aggression → weight toward trips/boats sharing the
  paired rank (this is exactly `cmtd2ab0`)
- three-flush board → weight toward suited combos
- otherwise fall back to strength percentile

This is a real modelling change and should not be rushed.

### Test plan

Both busts are exact repros with known hole cards, board, and villain. The
`+207` winner is the mandatory **should-not-trigger** control. Bar to ship:
both busts drop below their continue thresholds **and** the winner stays above
its. The table above shows the naive version fails that bar today.

### When not to do this

If action-conditioned ranges prove too fiddly to calibrate on the available
sample, the honest fallback is to leave equity alone and rely on the velocity
brake — which already prevents the *stack-off*, even while the equity number
stays wrong.

---

### RE-INVESTIGATION, 2026-08-31 (archive n=254) — STATUS: STILL HOLD

Re-run once the archive was deep enough to test board texture rather than
guess at it. **Decision unchanged, but the reasoning has changed: the signal
now looks real, and the blocker is sample size rather than a broken premise.**

**Correction to the record.** This item had been described as covering three
hands with a shared mechanism. It does not — only two:

| hand | board | paired? | mechanism |
|---|---|---|---|
| `cmtd2ab0` | `Ad Ah 2c Qd 8s` | yes (2) | trip aces beaten — texture |
| `cmtgzs2j` | `Jh Jd Js 5c Qd` | yes (3) | boat beaten by quads — texture |
| `cmtb7bys` | `2s Jc Qd 3h 5c` | **no** | top pair **outkicked** — *different problem* |

`cmtb7bys` is an unpaired board and is a kicker-domination issue. **Do not
fold it into this item.** A fix aimed at both at once would be aimed at
neither.

**The measured signal (89 archived hands with complete boards + hole cards):**

| board texture | our category | n | we were best |
|---|---|---|---|
| unpaired | 3+ | 5 | **3 (60%)** |
| paired (2+) | 3+ | 3 | 1 (33%) |
| **trips+ on board** | 3+ | **3** | **0 (0%)** |

A strong hand was best 60% of the time on unpaired boards and **0 of 3** on
trips+ boards. That is the concentration effect appearing exactly where the
mechanism predicts it should.

**Candidate conditions and their false-positive rates:**

- *paired board + our category 3+* → flags 6, **5 beaten / 1 best (17% FP)**.
  The false positive is real: `cmtge2sh`, a boat on `5s 3c 5c 4c 8h` where we
  were ahead.
- *trips+ on board + our category 3+* → flags 3, **3 beaten / 0 best**.
  Clean split — but on three hands.

The texture test is **deterministic** — max rank-count on the community cards,
read straight off the board, no model input. Same class of fact as
`made_category`, so it carries no circularity risk (PRINCIPLES.md #9).

**Why hold anyway.** n=3 is not a boundary test. Compare the standard the
stalled-hand guard had to meet before shipping: 49 outcome-joined hands, 5
losses caught, 0 winners touched, and a category boundary where widening by
one measurably cost a +42 winner. Here the clean variant rests on three hands,
and the wider variant already shows a 17% false-positive rate. Shipping now
would mean acting on the thinnest evidence in this project, in the one area
where a naive version has already been demonstrated to harm winners.

**Actionability threshold.** Trips+ boards with a strong hand occur roughly
**once per 30 archived hands**. Re-run the audit when the archive passes
**~500 hands** (~13 expected instances). If the split is still clean at that
n, this meets the same bar the stalled-hand guard met and becomes buildable.
The audit script (`scratchpad/texture_audit.py`) already exists and re-running
it costs nothing.

**Open design tension — read this before building anything here.** A texture
rule would want to be *more* cautious with a strong made hand on a dangerous
board. The **made-hand exemption** (`STACK_VELOCITY_MADE_HAND_EXEMPT = 4`)
currently skips the velocity brake for category 4+ **unconditionally, with no
reference to board texture**. These two directly conflict, and `cmtgzs2j` is a
live example where both apply at once: we held a boat (exempt, brake skipped)
on a trip-jack board (exactly the texture a rule would flag). Whichever is
built second must arbitrate against the first — this is a genuine design
problem, not a bolt-on. Note also that the exemption has confirmed value
(`cmtg76xo` +377, `cmtgk7w6` −352 forfeited before it existed), so weakening
it needs its own evidence, not just a texture argument.

---

## 2. Trajectory-based discrimination (flat-high vs rising-late equity)

### Why this exists

Three busts cleared a hard equity floor by a hair: **0.809**, **0.828**,
**0.954**. That is a pattern, not variance. But the decisive finding is that
**no threshold can fix it**:

| hand | final equity | outcome |
|---|---|---|
| `cmtd5vh7` | 0.809 | −401 |
| `cmtcarsk` | 0.828 | bust |
| **`cmtd6j6v`** | **0.906** | **+207 WIN** |
| `cmtd2ab0` | 0.954 | bust |

The winner sits *between* two busts. In the 0.80–0.96 band the equity **level**
carries no discriminating signal. Raising the floor cannot work; removing it
folds the winner (verified — it would have folded `cmtd6j6v` outright).

### The candidate signal

Equity **trajectory** across streets:

```
WIN  cmtd6j6v   0.574 → 0.282 → 0.457 → 0.584 → 0.906   rose late (+0.32 final street)
LOSS cmtd5vh7   0.584 → 0.884 → 0.809 → 0.839 → 0.804   flat-high, drifting down
LOSS cmtd2ab0   0.921 → 0.950 → 0.952 → 0.954           flat-high, +0.03 total
LOSS cmtcarsk   0.689 → 0.641 → 0.888 → 0.828 → 0.828   flat at the end
```

Hypothesis: **flat-high equity while commitment escalates** is the
dominated-hand signature — we hold a made hand that isn't improving and keep
paying. **Rising-late** means we were drawing and got there.

### Window size and history required

Poker gives at most 4 streets, so at most **3 deltas per hand** — and the useful
window is 2 (current vs previous street). This is a very short signal; it cannot
be smoothed or averaged. It is only meaningful postflop, and realistically only
on **turn and river**, where 2–3 prior observations exist.

### State problem

`decide_live` is stateless per poll and the process is short-lived. Equity
history must persist per `tableId`, keyed and expired like
`opponent_stats_cache.json`. Prior-street equity **cannot** be recomputed after
the fact — the earlier board is gone — so it must be recorded as it happens.
That means shipping the *recording* first and the *signal* later.

### Three confounders that could sink this

1. **Estimator variance shrinks as the board fills.** Preflop equity vs 2
   unknown cards and river equity with 5 known cards are not the same quantity.
   A "rise" may be the estimator sharpening, not the hand improving.
2. **`num_opponents` changes between streets.** Equity moves when opponents
   fold, for reasons unrelated to our hand.
3. **`range_strength` already discounts later-street equity** (up to 0.7 on
   turn/river). That *artificially* depresses late equity — making genuinely
   improving hands look flat. Trajectory must be computed on the
   **undiscounted** equity, which means storing both values.

Confounder 3 is the dangerous one: measuring trajectory on the discounted
number would invert the signal.

### Interaction with existing checks

Preferred: a **modifier**, not a fourth independent brake. Three overlapping
brakes already interact in ways that took three busts to understand.

- flat-high trajectory + escalating commitment → **tighten** the velocity cap
- rising-late trajectory → **loosen** it toward the current value

This gives the level check the discriminator it currently lacks, without adding
another hard gate that can be cleared by a hair.

### Test plan

Shadow mode, same as the velocity brake — but note it needs the recording layer
live for a full run *before* the signal can be evaluated at all. Four hands is
not a validation set; this needs several hundred hands with recorded
trajectories before any threshold is chosen.

---

## 3. Backlog — real holes, nothing broken yet

Both are the same underlying issue as the busts: **an undiscounted number
treated as reliable in a band where it isn't.** Neither has caused a
confirmed loss. Neither should be patched late at night.

### 3a. `value_thresh` gets no aggression discount

`live_agent.py:509`

```python
value_thresh = 0.68 - pos_bonus * 0.3                              # no bumps
raise_thresh = 0.72 - pos_bonus * 0.3 + escalation_bump + opp_bump # both bumps
```

Two adjacent thresholds, one discounted for opponent aggression and one not.
Every value **bet** is gated on the undiscounted one — including the `−83`
river barrel and the `−401` flop bet.

Partial mitigation already exists: `range_strength` lowers the equity *input*,
so the gap is narrower than it looks. Fix is likely a one-line symmetry
restoration, but it widens folding behaviour and deserves a simulation pass
against the historical set first, exactly like the preflop cap got.

### 3b. Bet sizing scales up at `equity > 0.82`

`live_agent.py:513, 520`

```python
amount = sized_bet(0.75 if equity > 0.82 else 0.5)
```

A bare constant that sizes **up** (0.5-pot → 0.75-pot) precisely in the band
where equity is least trustworthy. It makes losses larger exactly where the
model is most likely wrong. The `−401` hand's flop bet at 0.884 triggered this.

Note this is a *sizing* rule, not a safety gate — it cannot cause a bad call,
only make a bad bet bigger. Lowest urgency of anything here, but it is the
clearest example of the pattern: a bare `equity > X` in the unreliable band.

---

## Confirmed structural gaps (benchmark/code-verified, not backtest-only)

Unlike §1 (texture, on hold pending data) and §3a/board-plays/PR1/PR2 (all
ruled out on backtest data), these two findings don't depend on contested
outcome data — one is external benchmark comparison, the other is true by
code inspection alone.

### A. Preflop passivity (PFR deficit)
- VPIP 0.527 (highest in top-150 field) vs PFR 0.104 — call:raise ratio
  5.1:1, where top-10 agents run 1.5–1.7:1. Confirmed via
  `/competition/leaderboard` + `/texas/agent-stats`, n≈1,200 hands per
  benchmark agent.
- v5's historical regression is NOT the same change (v5 lowered VPIP via a
  Chen gate; this is about raising more of what's already played) —
  reconstructed from surviving source, see FINDINGS.md.
- Two design attempts (PR1: raw equity threshold, PR2: hand-class +
  position aware) both correctly avoided v5's mechanism, both backtested
  negative on n=4-17 conversions — too thin to trust, and structurally
  blind to fold equity (a raise's value largely comes from folds, which
  a static replay of past hands can't measure).
- STATUS: real, confirmed gap; no safe backtest path forward. Any next
  step requires a live trial, which has not been taken given no design
  has yet produced a stable/trustworthy conversion set.

### B. No bet-size polarization
- `sized_bet(0.75 if equity > 0.82 else 0.5)` — sizing keys only on
  equity, in a two-value band. Confirmed identical median bet_to_pot
  for weak hands (cat 0-1: 0.71) and strong hands (0.72) — true by
  code inspection, not outcome-dependent.
- Explains AF gap (0.40 vs top-10's 1.26-7.44) better than the original
  "bets too small" hypothesis (bluffPct 0.62, mid-pack — bets aren't
  under-sized, they're uninformative and infrequent).
- Turn/river sizing also runs 1.5-1.65x the standard GTO-adjacent
  benchmark (67/50/40% by street) — flat ~0.7-0.8 pot regardless of
  street.
- STATUS: same fold-equity blind spot as (A) — can't backtest whether
  size variation would generate more folds. No fix proposed.

### C. Fold equity — exists, measured, but not priceable (Stage 2, PARKED)

Built in two versions on 2026-09-02, both parked. `FOLD_EQUITY_ENABLED = False`,
code retained behind the flag.

**v1 — unconditionally bet-positive.** Applied `P(fold)` as a universal constant
to every checking spot. Fired on **257 of 271 archived checks (95%, 44.6% of all
postflop decisions)**. The algebra shows why: at half-pot the comparison reduces
to `bet when eq < 1.38` — equity cannot exceed 1.0, so the model could never
output "check" at all. Root cause was not the constant's value but its **domain**:
0.55 was measured on bets we *chose* to make (rare, value-heavy, board-selected)
and then applied to a different population (all spots where we currently check).

**v2 — domain-restricted.** Narrowed to a semi-bluff band (equity 0.25–0.55,
single opponent, non-river) and corrected `EV_check`, which previously used
`equity × pot` and so credited checking with nothing beyond the current street —
a permanent thumb on the scale for betting. Fired at **12.8%** of postflop
decisions, concentrated on the turn (Turn 43 / Flop 6 at `MIN_EDGE=0.05`).

**Killed by sensitivity, not by firing rate.** At `MIN_EDGE = 0.10` — twice the
inputs' stated uncertainty — **71% of surviving firings flip** when
`P_FOLD_SINGLE` and `CHECK_FUTURE_VALUE` both move 0.05 against betting:

| perturbation | flips at MIN_EDGE 0.05 | at 0.10 |
|---|---|---|
| `P_FOLD_SINGLE` −0.05 | 61% | 50% |
| `P_FOLD_SINGLE` +0.05 | 0% | 0% |
| `CHECK_FUTURE_VALUE` +0.05 | 39% | 21% |
| **both against betting** | **78%** | **71%** |

The asymmetry is directional — perturbations *favouring* betting flip nothing.
`P_FOLD_SINGLE` dominates the arithmetic (it multiplies the whole pot, while
equity touches only the called branch), so raising `MIN_EDGE` selects spots
further up the same slope rather than buying robustness.

**What survives.** The empirical measurement — n=172, the largest clean sample
in this project. Opponents fold **more than break-even at every bet size we have
actually used**:

| bet-to-pot | P(fold) | α (break-even) | |
|---|---|---|---|
| 0.40–0.60 | 0.492 | 0.333 | bluffs +EV |
| 0.60–0.80 | 0.487 | 0.412 | bluffs +EV |
| 0.80–1.00 | 0.500 | 0.474 | bluffs +EV |
| ≥1.00 | 0.643 | 0.556 | bluffs +EV |

Overall 0.529; heads-up 0.658. **The field folds MORE than a generic prior, not
less** — contrary to the assumption that a loose-passive field folds less. Most
likely because our bets are rare and therefore credible. **Fold equity exists and
is being ignored: that claim stands.**

**What does not survive.** Pricing it precisely enough to act on marginal spots.
That needs `P_FOLD_SINGLE` known to better than ±0.05. The measurement carries
acknowledged selection bias, and the API does not expose per-opponent
fold-to-cbet (`agent-stats` returns vpip/pfr/threeBetPct/af/bluffPct/wtsd/wsd
only), so there is no independent source to tighten it against.

These are two different claims and only the first is supported: *fold equity
exists* is measured; *this spot is +EV to bluff* is not.

(A) and (B) point the same direction: strategy is too uniform
(whether to raise, how much to raise) to be hard to play against. (C) is the
attempt to fix the reasoning gap underneath them, and it establishes the
opportunity is real while failing to make it actionable.
Neither has a safe backtest path — next step for either requires a
live trial, and there's no season clock forcing urgency on that
decision.

**Cross-reference:** (B) is the same code site as §3b above, which flagged
the `equity > 0.82` cutoff from the loss-analysis side. §3b asked whether
that threshold sizes *up* in an unreliable band; (B) is the stronger and
more general statement — the rule has only two output values and ignores
hand class entirely, so it cannot polarize in either direction. Treat (B)
as superseding §3b's framing.

---

## Sample-size caveat, applies to everything above

At ~0.29–0.37 hands/min with a 1-table concurrency cap, several hundred hands
is **13+ hours** of continuous running. Every threshold in this document that
is currently justified by 3–4 hands should be treated as a hypothesis, not a
finding, until that sample exists. The velocity brake is the only item so far
validated against a real historical set (37 outcome-matched hands, 0 false
positives), and even that is thin.

---

## 5. Velocity brake: the pot-odds exemption, and the one hand it cannot save

**Shipped 2026-09-03** (`STACK_VELOCITY_POT_ODDS_EXEMPT = 0.12`). Recorded here
because the *rejected* design is the useful part.

### What shipped
The brake now skips entirely — before the cap, same shape as the made-hand
exemption — when all three hold:

    pot_odds = to_call / (pot + to_call) <= 0.12
    equity >= 3 x pot_odds
    made_category >= 1

Rationale: the brake exists to distrust our own equity *estimate* when
commitment spikes. Below some price that distrust is moot — any made hand holds
at least ~12% against any range, so being wrong about *which* made hand we have
cannot make the call -EV. Above that price the estimate matters again.

### The design that was rejected, and why
The obvious formulation — "skip when equity is *far above* pot odds" — does not
survive the real hands:

| hand | street | action | equity | pot odds | opp raises | correct verdict |
|---|---|---|---|---|---|---|
| `cmtktqkt` | Turn | call | 0.955 | **0.049** | 1 | exempt (trips, 19.5:1; brake forfeited ~6000) |
| `cmtkwn8t` | Turn | raise | 0.861 | **0.282** | 0 | arguably exempt (−69) |
| `cmtcarsk` | Turn | raise | 0.828 | **0.294** | 0 | **BRAKE** (the bust the brake was built for) |
| `cmtcarsk` | Turn | call | 0.828 | **0.209** | 1 | **BRAKE** (fatal) |
| `cmtd2ab0` | Flop | call | 0.954 | **0.397** | 3 | **BRAKE** (−1000) |

`cmtkwn8t` (0.282) and `cmtcarsk`'s turn raise (0.294) are **0.012 apart**, have
the same `opp_raises`, and both show ~0.85 equity. No threshold on pot odds, on
the equity-minus-pot-odds gap, or on the equity/pot-odds ratio separates them.
And `cmtcarsk`'s *fatal* call is **cheaper** (0.209) than the hand we would most
like to exempt — so any bar loose enough to save `cmtkwn8t` un-brakes the
original bust.

This is the recurring shape named at the end of PRINCIPLES.md: a gate cleared by
a hair. The response was to move the bar to where the argument is actually
sound rather than to where it separates the two examples.

### Consequence, accepted knowingly
`cmtkwn8t` stays braked. That fold was probably −EV (≈ +193 EV surrendered,
−69 realised). `test_velocity_brake.py` asserts it **stays** braked, with a
comment saying why, so the cost is recorded rather than quietly absorbed.

### Open question
`opp_raises >= 2` was the one clean separator found (`cmtd2ab0` had 3; every
hand we want exempt had 0–1). It was **not** used, because it does not help with
the `cmtkwn8t` / `cmtcarsk` pair — both are 0 — and adding an unused
discriminator would be false precision. If more brake firings accumulate it is
worth revisiting as a second condition.

---

## 6. Preflop branch structure — where strategy changes have to be wired

**Found 2026-09-03 from v33's logs.** Preflop `decide_live` has three branches,
and which one a decision lands in is not obvious from the code:

| branch | condition | share of decisions | notes |
|---|---|---|---|
| A | `to_call <= 0` | **7 / 1080 (0.6%)** | Nearly unreachable: preflop we are always facing at least the big blind. |
| B | facing a raise (`current_bet > bb`) | 634 / 1080 (59%) | The pool opens a lot; most of our preflop decisions are already defensive. |
| C | limped pot / only the BB to call | 439 / 1080 (41%) | **This is the real raise-first-in spot.** |

v33 wired the Stage 1 RFI table into branch **A**. It fired on 0 of 102 preflop
decisions that run, and `value_open` never appeared in the log once. The 3-bet
half (branch B) worked. Moved to branch C on 2026-09-03, together with the
iso-raise sizing (`ISO_RAISE_BASE_BB = 4.0`, `+1.0` per limper).

**Projection discipline:** the v33 pre-flight projected PFR 0.213 and the run
delivered 0.136. The projection scored hands by *card class* — "would the RFI
table open this hand?" — and never asked which branch the decision would take
or what the opponents had already done. Any future preflop projection must be
computed against the observed branch distribution
(`scratchpad/stage1_project_v2.py`), not against a hand-class table.

### 6a. Seat distribution — measured, and it is NOT what the block count implied

Checked 2026-09-03 before widening the tiers, because the "69% EARLY/HJ" figure
that motivated the change came from a script that reconstructed table size
incorrectly.

**The table is 6-max.** Every `position_frac` in 1181 of 1186 logged preflop
decisions is a multiple of 0.2, so `n = 6` exactly and `_rfi_position`'s labels
are correctly specified for the table we actually play. The tier tables were
**not** mis-specified against table size.

**We are not seated early unusually often.** Per hand: SB 23.9%, BB 11.4%,
EARLY 18.2%, HJ 17.0%, CO 11.4%, BTN 18.2%. EARLY+HJ is **35.2%**, against
33.3% for a uniform rotation.

**Branch C is what skews early, and it does so by construction.** 71.4% of
branch-C decisions come from EARLY/HJ — but that is mechanical: from CO/BTN
somebody has usually raised already, which routes the hand to branch B. The
late seats, where the RFI tables are widest, almost never *reach* the open
branch. So the wide end of the table was doing very little work. That is the
real justification for widening, and it is a different claim from "we get
seated badly".

The earlier 69% figure was produced by reconstructing `n` as
`num_opponents + num_pending + 1`, which counts only ACTIVE seats and therefore
drops everyone who has folded. `_compute_position` uses `len(seat_nums)` — every
seat. The two disagree, and the reconstruction gave wrong position labels.
Use the logged `rfi_pos`, or derive the label from `position_frac` with n=6.

### 6b. Tier widening (v35)

`_RFI_MAX_TIER`: EARLY 1 -> 2, HJ 2 -> 3. One step each, deliberately.

| | qualifying rate on v34's 42 branch-C decisions |
|---|---|
| current | 5/42 = 11.9% |
| widened | 9/42 = 21.4% |

Of the 9 previously position-blocked hands, **4 now open — all of them HJ tier
3.** The five blocked EARLY hands are all tier 3 and stay blocked, so the EARLY
change unblocked nothing in that sample. Across all history only **4.7%** of
branch-C EARLY decisions hold a tier-2 hand, against **23.3%** of branch-C HJ
decisions holding tier 3. **Essentially all of the expected gain is the HJ
change**; EARLY is along for the ride and should not be credited if PFR moves.

Branch-aware projection over 1007 hands: PFR 0.177 -> **0.203**, VPIP 0.558 ->
0.562 (additive, as intended), call:raise 2.16:1 -> 1.77:1.

### 6c. Iso-raise (§4) — implemented, NOT validated, and stop waiting

`value_iso_raise` has had **42 of 42** branch-C decisions with zero limpers in
v34, and `num_opponents` (computed by an independent code path) agrees at 0 in
all 42. **This pool does not limp** — anyone who acts before us raises, which
routes to branch B.

Status: reachable (unlike branch A, which was genuinely unreachable), verified
in the harness at 4/5/6/7 bb for 0/1/2/3 limpers, and **exercised zero times in
production**. Do not expect production data on it, and do not record it as
validated. If the pool ever changes, it will start firing on its own.

### 7. The 3-bet gate bypassed its own threshold (FIXED v36)

v35 lost **854 of 1000 chips in 45 hands**. Five hands account for -851; three
account for -623. All three had one shape: we 3-bet, opponent 4-bet, we 5-bet,
then a circuit breaker fired with the money already committed and we folded.

Cause: the gate at `live_agent.py` tested **card class only**.

```python
if (STAGE1_RFI_ENABLED and _t is not None and _t <= 2 and has("raise")):
```

`raise_thresh` is computed on the line directly above and never consulted on
this path. **All 8 of 8** `value_3bet` firings in v35 were below their own
raise_thresh. And with no re-raise counter, a tier-1/2 hand re-raised
indefinitely — `escalation_bump` was computed (0.1, 0.2) and discarded.

That is the same failure `_opponent_raises_this_street` was written for: its
docstring cites the round-51 bust ("busted the whole 1916-chip stack in a
three-raise turn war at a self-computed 72-77% equity the whole way"). **The
guard existed and the gate routed around it.** Worth generalising: a safeguard
that another code path can bypass is not a safeguard, and the bypass will not
show up in the safeguard's own tests.

Fix: `opp_raises <= 1`. Deliberately **not** `strength >= raise_thresh` — that
would also suppress opening 3-bets and confound the live Stage 1 PFR
measurement. The cap stops re-fires only. Verified against the v35 log: all
three escalated re-raises (cmtloxo0 104, cmtlp5gg 77, cmtluq1d 83) blocked, all
five first-3-bets preserved.

**Attribution split.** Two gates shared the message `"premium hand, re-raising
for value"`, so the log could not distinguish a 3-bet that BYPASSED raise_thresh
from one that CLEARED it — the exact distinction this post-mortem turned on, and
the reason all 8 sub-threshold firings looked normal. The tier path is now
`value_3bet_tier`; the merit path keeps `value_3bet`. Post-fix, AA still
re-raises a 4-bet war (clears the escalated 0.866 on merit) while KK calls and
AKo/ATs fold — the escalation ladder discriminates again.

### 7a. OPEN — escalation_bump is computed and discarded on the 3-bet path

Flagged, deliberately not fixed in the same change: it moves aggression on a
different axis from the re-fire cap. Now that the cap is in, decide separately
whether `escalation_bump` should feed the tier gate's threshold. Note the cap
already gets most of the benefit — blocked re-raises now fall through to the
merit path, where escalation_bump *is* live in `raise_thresh`. Measure v36
before adding more.

### 7b. Circuit-breaker audit — one bug, and why the other two were safe

`STACK_RISK_CAP` folded a 464-chip pot on cmtlp5gg (-231) with checking legal:
it tried to downgrade raise->call, which needs `to_call > 0`, and fell through
to `elif has("fold")`. Audited all three breakers and all 8 fold sites:

- **Pre-selection** branches (5 sites) choose an action inside an enclosing
  `if to_call <= 0:` split, so a fold arm is unreachable on a free check even
  where fold is ordered before check. Safe **structurally, not by intent** — and
  the misleading ordering at four safe sites is good camouflage for the one
  unsafe site.
- **Post-processing overrides** (STACK_RISK_CAP, velocity brake) run on whatever
  was already chosen, in every context including a free check, so each must test
  `to_call` itself. The velocity brake does. The stack risk cap did not. That
  asymmetry was the whole bug.

Pinned by `test_never_fold_free_check.py`, which asserts the property
end-to-end through `decide_live` over 96 states rather than re-reading control
flow — reading is how this survived. Confirmed to fail without the fix (8/96,
exactly the TsTd flop/turn shape).

### 8. The velocity brake's second fallthrough-to-fold (FIXED v38)

`cmtlxu0b` (v37, -50): two pair on Jd 4h 7c Tc, equity 0.866, pot 140, to_call
38 — **3.7:1 on a +116 EV call, folded.**

The brake wanted to downgrade raise->call. Its guard requires the downgrade
target to be under the cap; `call_delta = 38/63 = 0.603` against a turn cap of
0.20, so the call arm failed and control fell through to `if can("fold")`.

**This is the second instance of the shape found in §7b, and my audit there
cleared the brake.** The brief asked me to grep for "a fallthrough to fold when
the intended action isn't legal"; I checked that literal condition, found the
`to_call <= 0 -> check` guard, and reported the brake clean. The brake's
intended action here was *legal but unaffordable*. Same fallthrough, same cost,
a trigger one word away from the one I was looking for.

**Not a short-stack artifact.** The cap is a fraction of stack, so the leak
scales with it — at a full 1000 chips a 250-chip turn bet into a 900 pot folds
a **+999 EV call**. The 146-chip stack made it fire sooner, not exist.

Fix: a refused raise falls through to `call`, never to `fold`. The brake governs
ESCALATION; when it refuses a raise, calling is strictly smaller than the action
just refused, so folding overshoots its own remit. **This requires no trust in
the equity estimate** — it never asks whether the call is +EV, only that it is
cheaper than the refused raise. That is what separates it from the pot-odds
exemption we rejected.

Scoped to raise/bet/all-in. When the PROPOSED action is itself a call there is
no larger refused action, and folding stays correct.

**Blast radius, measured over 4,070 logged decisions in 125 logs:** the brake
fired 67 times; **9 verdicts change**, all folds becoming calls, all with a
made hand (categories 1-4). Both busts are untouched.

### 8a. cmtkwn8t is recovered — and the "inseparable" proof was axis-bound

§5 recorded that cmtkwn8t (-69) must stay braked forever, because exempting it
needs a pot-odds bar >= 0.282 which also exempts cmtcarsk's fatal call at
0.209. That reasoning was correct and remains correct: **on the pot-odds axis
the two cannot be separated**, and cmtkwn8t is the more expensive call of the
pair.

They separate cleanly on an axis nobody examined — the action type:

    cmtkwn8t   action="raise", to_call  94   refused RAISE -> now calls
    cmtcarsk   action="call",  to_call 933   refused CALL  -> still folds

The accepted cost is recovered for free, and cmtcarsk's protection is untouched.
Worth keeping as a worked example: *"provably inseparable" was true of the
property being measured, not of the two cases.* When a separation argument
concludes a cost is unavoidable, that conclusion is scoped to the axis it was
argued on — the next question is which other properties differ.

### 9. COMPETITION SWITCH at v38: Playground S18 -> Eval S1 (PVE)

**S18 and S1 numbers must never be pooled.** PFR, VPIP, call:raise and the
branch B/C mix are all functions of what the OPPONENTS do. S18 is a live 6-max
pool; S1 is a **fixed reference panel** — a different population, so this is
Principle 11 exactly. Stack context differs too: S18 hands were played at
2-146 chips against 950-2150 chip opponents, S1 resets the stack each hand.

**Only the three mechanism checks transfer**, because they are properties of
our own code rather than of the pool:
  1. does `value_3bet_tier` fire at all (0 firings in 3 consecutive runs)
  2. does the escalation cap block at `opp_raises >= 2`
  3. does the brake fall through to `call` rather than `fold` on a refused raise

Enforcement, rather than a note nobody reads: `live_agent` logs a greppable
`=== COMPETITION` banner at start, and `runlog.competition_of()` /
`assert_same_competition()` make a cross-competition pool raise instead of
silently producing plausible-looking numbers.

Baseline saved to `.baseline_eval_s1.json` (participant null — never joined).

**Why S18 was abandoned.** v37 ground from 146 chips to 2, and the rebuy is
unreachable from there: the arena allows a rebuy only "after total chips drop
below bigBlindChips", the big blind is 2, and we held exactly 2 — while the
arena still reported `chipState: "available"`, so the agent's own
`chipState == "busted"` trigger could not fire either. Stuck in the gap between
two conditions, playing 1bb, where no 3-bet war can occur and the cap under
test is unreachable by construction.

### 9a. Eval Open S4 is PAID — do not join it

**S4 charges a per-run fee in USD-T0 on X Layer and pays out from an entry
pool.** It was proposed as a venue on the strength of `/texas/rebuy-status`
returning "Agent has not joined this competition yet" with `maxRebuys: 9999`.
**The join-state endpoint says nothing about cost**; the fee appears only in the
competition's `description`. Read the description before treating any
competition as a venue.

It is also heads-up against a fixed bot with its own `skillFile`
(`eval-open.md`, where every other poker competition uses `texas-holdem.md`),
so it could not produce 6-max Stage 1 numbers even if it were free.

Now guarded in code: `PAID_COMPETITIONS` causes `main()` to refuse before the
first network call, and `classify_join` treats HTTP 402 as terminal.

### 9b. Tournament S17 — deferred, deliberately

Free and a live 6-max pool, so the better strategic match. Single irreversible
buy-in, no rebuys, and a bust ends the season. Spending that on a run whose
purpose is verifying three code paths fire is a bad trade; it becomes the right
venue once the mechanisms are verified.

### 10. Benchmark-mode support (v38) — implemented, NOT yet run

Eval S1 rejects `/texas/join` with `409 benchmark_lobby_unavailable`:
"Benchmark competitions use /texas/benchmark/start instead of the matchmaking
lobby". Entry and polling are a separate surface.

    POST /texas/benchmark/start   {competitionId}          -> starts a match
    GET  /texas/benchmark/status  -> {match, table, participant}

against the lobby path's `{tables, activeTables, lobby, participant}`.

`benchmark_poll()` normalizes the benchmark envelope into the pending-actions
shape, so **the decision path is untouched** — `decide_live` and everything
under it never learn which mode they are in. Two poll sites plus the finalize
snapshot now go through `poll_pending()`.

**Mode is DERIVED, not configured.** Rather than a hardcoded set of benchmark
competition ids (one more thing to go stale, cf. the twice-stale log paths),
the agent reads the arena's own `benchmark_lobby_unavailable` error and
switches. It cannot disagree with the server, because it comes from the server.

**`_table_is_actionable()` matters more here than it looks.** pending-actions
pre-filters to tables awaiting our action; `benchmark/status` appears to return
the table regardless of whose turn it is. Without the guard the loop would
re-decide and re-POST the same table in a tight spin during the panel's turn.

#### What could NOT be learned without spending a scored attempt

Exhausted first, all negative: no skill file over the API (7 route shapes
tried), no local copy, no OpenAPI/swagger spec (6 tried), no public docs page,
no `/texas/benchmark/attempts` or `/history`, no leaderboard route.

Confirmed WITHOUT starting anything, by probing validation errors:
  - `POST /texas/benchmark/start` requires exactly `{competitionId}`
    (400 on empty body: "body must have required properties competitionId")
  - an unknown competitionId 404s without side effects
  - `GET /texas/benchmark/status` returns keys `{match, table, participant}`,
    all null while idle

Still unknown, and **only learnable by starting a scored run**:
  - the populated shape of `status.table` (assumed to be the standard table
    object, since it is the same engine — assumed, not verified)
  - the response shape of `/start`
  - **whether `/start` consumes a limited attempt, and how many exist**

`benchmark_poll()` is therefore written to tolerate a single object, a list, a
string, or garbage, degrading to "nothing to do" rather than raising; and
`_log_shape()` dumps the first raw payload of each kind so the first run is
also a shape-discovery run and a second is not needed to find out what came
back. Pinned by `test_benchmark_mode.py` against synthetic payloads — no
network, no attempt consumed.

#### Open question blocking the first run

`maxRebuys: 0` and "a fixed number of reset-stack hands" producing "a raw
bb/100 leaderboard" reads as a scored, possibly one-shot attempt. Nothing in
the API exposes an attempt count, so this cannot be settled by probing. Starting
a run is the only way to find out, and if it is one-shot, finding out that way
spends it.

---

## VENUE STATE as of 2026-09-03 (read this first when picking the project back up)

No agent is running. Nothing is queued. The decision surface below is complete
as of this date — it should not need re-deriving.

**UPDATED after v39 (2026-09-03 19:36): the S18 wedge RESOLVED ITSELF by
playing on. S18 is live and playable again at a competitive stack.**

| venue | id | state | cost / risk |
|---|---|---|---|
| **Playground S18** | `cmtlk9djt0mtz7njb1lf85704` | **1055 chips**, 89 hands, rebuyCount 1 | free, **PLAYABLE — use this** |
| **Tournament S17** | `cmtlk922v0mtf7njb5z4rym96` | never joined | free, **one irreversible buy-in, no rebuys** |
| **Eval S1** | `seed_poker_eval_s1` | never joined, support BUILT | free, **unknown whether attempts are limited** |
| **Eval Open S4** | `cmsy5imvg2h6zeczzzw1fc7ya` | never joined | **PAID** — guarded in code, do not join |

### Playground S18: the big-blind wedge, and how it clears

**The wedge.** The arena permits a rebuy only "after total chips drop below
bigBlindChips". The big blind is 2. At exactly 2 chips, "below" is strict, so we
were one chip short of eligible — while the arena reported
`chipState: "available"`, so the agent's own trigger (`chipState == "busted"`)
could not fire either. Wedged between two conditions, playing 1bb, where no
3-bet war can occur and the Stage 1 mechanisms are unreachable by construction.

**It clears by playing on, and cheaply.** v39 ran 45 minutes for this alone:

    5 chips -> busted to 0 -> rebuy fired -> 1000 -> finished at 1055

**If this happens again, just run the agent.** The wedge is not a dead end and
does not need a venue switch, a Tournament buy-in, or an Eval attempt. It cost
27 minutes and 5 chips. Two supporting facts worth keeping:

  - **We never left the table when v37 was stopped.** The next join returned
    `409 max_concurrent_tables`, and the arena had been taking timeout actions
    on our behalf — 6 hands passed with no agent attached, chips drifting
    2 -> 5. Stopping the process does not vacate the seat.
  - **The rebuy path executed for the first time ever** (rebuyCount had been 0
    across every run this project has done). It is no longer unverified.

**One bug it exposed.** The `=== STACK RESET` marker printed
`rebuy #None: 0 -> None chips`: the rebuy response nests under `participant`
and the marker read the top level. It fired and was greppable — the part that
matters for sample splitting worked — but its numbers were wrong. It had been
written blind precisely because the rebuy had never run. **An observability
line is itself unverified code until it fires.** Fixed, with a fallback if the
shape ever flattens, and replayed against the real v39 payload.

**Tournament S17** is a live 6-max pool with a full stack, deferred because its
single irreversible buy-in is worth spending on verified code rather than a
mechanism check. **S18 being playable again removes the reason to consider it
at all for now** — S18 is free, has unlimited rebuys, and is the same 6-max
format. Revisit only if S18 becomes genuinely unavailable (season ends).

**Eval S1** has benchmark support built and parked (§10). Not started because
run one would be **partly shape-discovery** — the populated `status.table`
shape is unverified and unlearnable without starting — so it would not be a
clean mechanism check even if attempts are unlimited. **Also now unnecessary**:
its only advantage was reset stacks in a venue we could play, and S18 provides
that for free via rebuy. Keep parked; one line to start if ever wanted.

**Eval Open S4** charges a per-run fee in USD-T0 on X Layer. `PAID_COMPETITIONS`
makes `main()` refuse before the first network call and `classify_join` treats
402 as terminal. It is also heads-up against a fixed bot with its own skill file
(`eval-open.md`), so it could not produce 6-max Stage 1 numbers even if free.

## MECHANISM FIXES: verified in harness, UNVERIFIED in production

Three fixes are complete, tested, and had **never executed against a live
table** — not from neglect, but because no venue we could play would exercise
them: S18 was at 1bb where the spots cannot arise, S17 costs a single
irreversible buy-in, S1's cost is unknown, S4 is paid.

**That blocker is now GONE.** v39's rebuy put S18 back to a competitive stack
(1055 vs opponents at 954-1992), so the spots can finally arise. v40 is the
first run that can actually test these. Update this section with what it finds.

| fix | what must be observed in production | harness coverage |
|---|---|---|
| **3-bet escalation cap** (`opp_raises <= 1`) | `value_3bet_tier` fires on a first 3-bet; does NOT fire at `opp_raises >= 2` | `test_3bet_escalation_cap.py` |
| **Brake call-fallthrough** | a refused raise reaches `call`, never `fold` (`fallthrough: call_over_cap`) | `test_velocity_brake.py`, `test_never_fold_free_check.py` |
| **`stack_risk_cap` free-check guard** | never folds when `to_call == 0` | `test_never_fold_free_check.py` |

`value_3bet_tier` has fired **0 times in production across v35, v36, v37** —
in every case because the stack was too short for a 3-bet war to occur, not
because the gate is wrong. Do not read the zero as evidence about the cap.

The last two are also pinned by an outcome-level invariant (600 states through
`decide_live`, fails on any fold while a strictly cheaper legal action exists),
confirmed to catch both original bugs under isolated revert — 71 violations for
the brake, 8 for `stack_risk_cap`.

**Next session:** run on **S18** — it is playable and free. If it has wedged at
exactly the big blind again, that is not a blocker: run the agent and it clears
itself (see the wedge section above). Do not reach for S17's buy-in or an S1
attempt to solve a short stack; the rebuy handles it.

When reading any run log, analyse **post-rebuy hands only** unless you
specifically want the short-stack stretch. `branch_audit.py` does this by
default, splitting on `=== STACK RESET` (verified against the real v39 log:
2 post-rebuy preflop decisions vs 8 across both stretches). `--all-hands` opts
back in. The stack safeguards key on a FRACTION of stack, so pooling a 5-chip
stretch with a 1000-chip one silently corrupts exactly the measurement being
taken.

### 11. v40 RESULTS — first clean Stage 1 run (2026-09-03, S18, post-rebuy)

633 chips from 1054, **-421 over 71 hands** (162 cumulative, 47 won). No rebuy.
First run in the sequence with a competitive stack, so the first whose numbers
mean anything.

#### Mechanism checks: 2 of 3 confirmed

**1. `value_3bet_tier` FIRES — confirmed.** Zero across v35/v36/v37, twice here:

    cmtmazuc  QsAd  tier 2  oppR=1  -> value_3bet_tier
    cmtmd9xk  AdAh  tier 1  oppR=1  -> value_3bet_tier
    cmtmdxq5  AsTh  tier 3  oppR=1  -> value_3bet   (merit, cleared 0.622 v 0.589)

The label split works in production and separates what it was built to
separate: tier 1-2 through the tier gate, tier 3 forced to clear raise_thresh
on merit. That distinction was invisible under the old shared label.

**2. The escalation cap BLOCKED — confirmed, on the exact target scenario.**

    cmtme89m  oppR=2  AcKs  tier 1  ->  call (pot_odds_call)

AKo, tier 1, facing a SECOND raise. Pre-fix the tier gate fires and re-raises -
this is the cmtluq1d shape exactly (AJo, oppR=2, raised to 83, lost 83). It
fell through to the merit path and called. First real opportunity, correct
behaviour.

**3. Brake call-fallthrough — NOT confirmed.** Three brake firings, all
`FALLTHROUGH=None`, all via pre-existing arms. One missed by 0.011
(`call_delta` 0.235 vs cap 0.246). The path is genuinely rare; absence here is
not evidence against it, and the harness covers it.

#### Stage 1 numbers (post-rebuy, S18 only - not comparable to other venues)

    VPIP 0.614   PFR 0.229   call:raise 1.69:1
    branch B (facing raise) 42   branch C (open) 40      51% / 49%
    value_open 9 of 40 branch-C decisions, 0 missed opens

PFR 0.229 is above v35's 0.203 projection and well above the 0.108 that started
this. Branch mix is near-even vs 59/41 in v34 - a materially different
distribution at full stack, so the v34-era branch projections should not be
reused without re-measuring.

#### THE LEAK: multi-street call-downs (3 hands = the entire loss)

    multi-street call-down then fold (>=3 calls, ends in our fold)
        3 hands    net -316    mean -105
    all other hands
       44 hands    net  +91

**The other 44 hands are profitable.** Three hands are the whole deficit, all
the same shape - call, call, call, fold:

    cmtm9j09  -273  8cQs  Pr:call Pr:call Fl:raise Fl:call Tu:fold
    cmtmdsf0   -36  6c5s  Pr:call Pr:call Fl:call  Tu:call Ri:fold
    cmtmcoob    -7

**This confirms a prediction already written in the source.** The STACK_RISK_CAP
comment states that its fix "would NOT have prevented either loss" for the
v7_9/v7_24 pair and that catching "multi-street call-downs of this shape needs
a separate, lower whole-hand threshold (see FINDINGS.md)". That was written
from two hands with no measurement. v40 confirms it with numbers: each street
prices fine in isolation, nothing accumulates across streets, and
STACK_RISK_CAP at 0.5 is far too coarse to see it.

Not a folding problem and **not an equity problem** - the calibration work
(§12) rules the latter out directly. It is the absence of any state
representing "I have now called three times against sustained aggression".

### 12. EQUITY CALIBRATION — measured for the first time (n=1315)

`estimate_equity` deals opponents uniformly random hole cards (§1). Whether
that biases the number, and by how much, had never been measured despite every
threshold in the agent being expressed in those units.

Method: join logged decisions (which carry the estimate) to archived hands
(which carry the arena's `winners` field) by table id, bucket by estimate,
compare to realized win rate.

**Aggregate: the estimate is fine — slightly CONSERVATIVE.**

    est. equity      n    mean est    won      gap
    0.0-0.3        159      0.206    0.333   +0.128
    0.3-0.5        422      0.410    0.498   +0.088
    0.5-0.6        280      0.549    0.646   +0.098
    0.6-0.7        204      0.645    0.691   +0.046
    0.7-0.8        112      0.744    0.804   +0.060
    0.8-0.9        103      0.844    0.883   +0.039
    0.9-1.0         35      0.950    1.000   +0.050
    ALL           1315      0.528    0.609   +0.081

Monotonic, no inversions. The +0.081 has a clean explanation: "won" counts
winning the pot including uncontested, and the model prices **no fold equity**
(`FOLD_EQUITY_ENABLED = False`). We are earning ~8 points of fold equity and
counting zero — which quantifies the Stage 2 opportunity for the first time.

**But the aggregate hides the thing that matters. Conditioned on aggression:**

    cumulative aggression      n    mean est     won       gap
    0-0                      566       0.529   0.694    +0.165
    0-2                      494       0.507   0.540    +0.033
    2-4                      252       0.561   0.579    +0.019
    4-6                       29       0.560   0.379    -0.181   OVER-confident

**Calibration degrades monotonically as opponents get aggressive and flips
negative in the top bucket.** This is the first empirical support for the
velocity brake's founding premise, previously justified by one hand
(`cmtcarsk`) plus the a-priori argument. v40's `cmtm9j09` fold happened at
`aggression_score 7.0` — off the end of that table.

Caveats that must travel with this: **n=29** in the decisive bucket, and the
sample is hands we CONTINUED with — hands the brake folded are absent by
construction, so this is evidence about the premise, not a verdict on any
single fold.

**Two methodology bugs were found and fixed before this result was trusted**,
both of which produced a dramatic false finding (realized 0.104 vs estimate
0.451, a -0.347 "gap"):
  1. 65% of archived hands are ones we FOLDED. They settle with a full board
     and look analysable; asking "would our folded cards have won" measures
     folding, not calibration.
  2. The archive exposes hole cards for **all seats including folded ones**, so
     ranking our hand against every visible opponent asks "did we have the best
     hand at the table", not "did we beat whoever contested the pot".
Both fixed by using the arena's own `winners` field instead of reconstructing.
**Prefer the recorded answer to a derived one wherever the recorded answer
exists.**

### 13. RULE: any correction to `estimate_equity` must be DOWNSTREAM, per-consumer

Generalizes past the projection that produced it. `equity` is consumed by at
least six places, and **two of them are safeguards whose constants were tuned
against the UNCORRECTED number**:

    STACK_RISK_EQUITY_FLOOR = 0.80    fires MORE as equity drops
    STALLED_EQUITY_RELEASE  = 0.45    releases LESS as equity drops
    velocity brake pot-odds ratio     exempts LESS
    value_thresh / raise_thresh / isolate_thresh   fire LESS

Measured on a candidate aggression discount over 4292 decisions:

    decisions below STACK_RISK_EQUITY_FLOOR: 4020 -> 4045  (+25)
    decisions below STALLED_EQUITY_RELEASE : 2149 -> 2178  (+29)

**An upstream change ships four changes while testing one.** Any future
correction goes in at the consumer that wants it, with that consumer's constant
re-derived at the same time - or every safeguard silently moves.

### 14. AGGRESSION BACKFILL — ATTEMPTED, NOT USABLE

Goal: reconstruct `aggression_score` for historical hands so aggression
-conditioned proposals can be tested against `cmtcarsk` and `cmtd2ab0`, which
predate the field and log 0.0.

**Blocked: the raw event stream was never persisted anywhere.**

    hand_archive.jsonl     `recentEvents` key ABSENT from all 1255 records
                           (/texas/recent-tables omits it for settled tables)
    live_round_v*.log      logs the derived debug dict, never raw events
    live_session_log.jsonl participant snapshots only

`cumulative_aggression` is stateless over `recentEvents`, weighting each opponent
action by type (call 0.5 / bet 1.0 / raise 2.0 / all-in 2.5) and street
(0.5/1.0/1.5/2.0). Without the events it cannot be recomputed.

**The best available proxy fails validation.** `opp_raises_this_street` IS in
the old logs, so a proxy was built from it and checked against 3291 decisions
where the real value also exists:

    exact match      33.5%
    mean abs error   0.852        (the useful knee is at agg 3.0)
    where real > 0   mean real 2.10 vs mean proxy 1.02
    SAME BUCKET      45.6%

The confusion matrix kills it. Of decisions truly in the 4-6 bucket the proxy
places **9 of 122** there, sending 66 to 0-2 and 13 to zero:

              0    0-2    2-4    4-6     6+
    4-6      13     66     34      9      0
    6+        1     12      6     10      4

The high-aggression tail is the ONLY region an aggression-conditioned rule acts
on, and the proxy misclassifies almost all of it. **It is a different quantity
wearing the same name** - worse than no backfill, because it would look like
evidence. Not shipped, not written into the archive.

Note a prediction of mine that was wrong: I expected the proxy to under-count
*by construction* (it sees only raises). It over-counts 28.1% of the time.

**What would actually fix this: persist `recentEvents` going forward.** The
archiver writes whatever `/texas/recent-tables` returns; the live poll DOES see
events and could archive them per decision. That does not recover the busts,
but it stops the same wall being hit again.

### 15. TEXTURE AUDIT RE-RUN at n=369 — the narrow signal STRENGTHENED

§1's re-investigation set an explicit trigger: re-run at ~500 archived hands
(~13 expected trips+ instances). Archive is at 1255 (369 with complete board +
our hole cards), giving exactly **13** instances. Trigger met.

**Narrow candidate — 'TRIPS+ on board AND we hold cat 3+':**

    n=89  (previous):   3 flagged, 0 best, 3 beaten
    n=369 (now):       13 flagged, 0 best, 13 beaten     <-- STRENGTHENED

Against a base rate of 39% best for cat 3+ overall (48% on unpaired boards),
this is 0 of 13. The split is clean and got cleaner as the sample grew 4x.

**Wide candidate — 'paired board AND we hold cat 3+': WEAKENED.**

    n=89  (previous):  6 flagged, 1 best  (17% false positive)
    n=369 (now):      25 flagged, 8 best  (32% false positive)

The two candidates moved in OPPOSITE directions. Anyone re-reading this should
not collapse them - the wide variant is now clearly worse than it looked.

**The narrow variant now meets the bar the stalled-hand guard shipped on**
(49 outcome-joined hands, 5 losses caught, 0 winners touched): 13 caught, 0
winners touched, on a deterministic board-texture test that reads max rank-count
off the community cards and consumes no model output (no circularity, PRINCIPLES
#9).

Next step is a projection of what flagging these hands would actually DO -
which decisions change and at what cost - not implementation.

### 16. TEXTURE SIGNAL PROJECTION — DO NOT BUILD. The 0-of-13 was measuring the wrong thing.

§15 reported the narrow texture signal as 13 flagged / 0 best / 13 beaten, the
cleanest result in this project. Projecting what ACTING on it would do kills it.

**1. The 0-of-13 counts HAND STRENGTH, not money. Four of the 13 WON.**

    cmthh8o1  board 3s 2c 2d 2s 6s   us JsTs   payout +29
    cmtk504o  board 7d 6d Qd 6c 6h   us Ac3s   payout  +5
    cmtkspvu  board Qd 9c Tc Ts Td   us KhAc   payout +40
    cmtlv31v  board Ac Ad As Kd 3c   us 2sQh   payout +46

We did not hold the best hand shown in any of them - and were paid anyway,
**+120 chips**, because the better hands folded. A rule that folds these
forfeits that. "We were beaten at showdown" and "this hand lost money" are
different questions, and only the second one matters for a folding rule.

**2. Principle 10: 12 of 13 are actionable, but there is almost nothing to act
on.** `cmtjmjuu` only completes trips on the river, so no live rule could ever
have acted. Of the remaining 12, **9 had ZERO actions after the texture became
visible** - the money was already committed, or we checked/folded regardless:

    cmtgzs2j  Flop   2 raises   committed 285   lost
    cmtluv91  Turn   1 call     committed  12   lost
    cmtlv31v  Flop   1 call     committed  13   WON
    (the other 9)               committed   0

**Total commitment a rule could have prevented across the ENTIRE archive
history: 297 chips of losses** - and it would also have to give up the 13-chip
call on a hand we won, plus the +120 above.

**3. False positives.** A LIVE rule (firing on the visible board, not the final
one) fires on 11 hands: **4 won / 7 lost, a 36% false-positive rate**.

**4. Zero overlap with existing safeguards** - the one thing that went in its
favour. None of the 12 had `velocity_brake`, `stack_risk_cap` or
`stalled_guard` fire. It would be a genuinely unique contribution, of almost
nothing.

**Verdict: do not build.** ~297 chips of preventable loss across all recorded
history, against forfeiting 4 winning hands and a 36% false-positive rate. The
signal is real - trips+ on board really does mean our strong hand is usually
dominated - but it arrives too late to act on and costs more than it saves.

### 17. PRINCIPLE: "best hand" and "won the pot" are different measurements

Third time in one session that conflating them flipped a conclusion:

  1. Calibration v1: counted hands we FOLDED as losses -> a false -0.347
     "over-confidence gap".
  2. Calibration v2: ranked us against ALL seats with visible cards, including
     folded ones -> a 12% win rate artifact.
  3. Texture signal: "0 of 13 best" looked decisive; 4 of the 13 won +120 chips
     because better hands folded.

Every one of these made the agent look worse than it is, and two of them nearly
produced a shipped change. **The arena's `winners` / `payoutChips` fields are
ground truth for money; hand-rank comparison answers a different question.**
Use rank comparison only when the question really is about hand strength, and
never as a proxy for profit - fold equity lives in exactly that gap, and this
agent earns roughly 8 points of it (12).

### 18. PRE-REGISTERED DECISION BAR: `aggression >= 4` as a calling filter

**Written 2026-09-07 BEFORE the next checkpoint, so the bar is not set after
the data arrives.** Same discipline as §1's actionability threshold (~500
archived hands) and the concentration threshold that gated the `pot_odds_call`
investigation. Both of those held; this one is to hold too.

#### The candidate

Among `pot_odds_call` hands (the largest identified leak, ~-3035 over 107
hands, and pre-existing code that neither shipped flag touches), the signal
`aggression_score >= 4` at the calling decision currently separates:

    signal                      lose_n  lose_net   win_n  win_net   hand ratio
    aggression >= 4                 10    -1653       3     +190       3.3:1
    made_category <= 1              84    -2477      18     +284       4.7:1
    to_call > 25% of pot            66    -2507      18     +298       3.7:1

`aggression >= 4` catches **47% of the total loss in 10 hands** while touching
3 winners. It is the only candidate whose ratio has been TIGHTENING as the
sample grew; the other two have drifted or stalled.

It is also mechanistically motivated rather than fitted: §12 measured our
equity estimate at **-0.181 over-confident in the 4-6 aggression band**, and
`aggression_score` is computable at decision time from `recentEvents`
(PRINCIPLES #10), consuming no model output (#9).

#### THE BAR

- **BUILD** if the hand ratio improves past **~5:1** as the sample grows.
- **DO NOT BUILD** if it plateaus near **3:1**. That is worse than the bar the
  stalled-hand guard cleared (49 outcome-joined hands, 5 losses caught, **0
  winners touched**), and "best signal currently available" is not a reason to
  ship a filter that folds one winner for every three losers avoided.
- **REPORT WHICH HAPPENED.** Not a re-framing, not a different cut of the data,
  not a composite signal invented afterwards to clear a bar the simple one
  missed. If it plateaus, the finding is "the sample could not separate these",
  which is a real result and the honest one.

#### Why this needs pre-registering

Three findings this week reversed on more data: a +4788 "net" that was gross
winnings, `semi_bluff` at +91 then +4 then +245, and a texture signal whose
"0 of 13 best" turned out to include 4 hands that WON. Every one of them looked
conclusive at the sample size where it was first examined. The failure mode
here would be to notice that 3.3:1 is disappointing and go looking for a
conjunction (`aggression >= 4 AND board paired`, say) that clears 5:1 on 6
hands - which is fitting, not finding.

#### Anchor for comparison at the next checkpoint

    n=1046 attributable, 107 pot_odds_call hands
    aggression >= 4: lose_n 10, lose_net -1653, win_n 3, win_net +190
    hand ratio 3.33:1, chip ratio 8.7:1

## §19 AGGRESSION FOLD — SHIPPED 2026-09-10 (`AGGRESSION_FOLD_ENABLED`)

Refuse the postflop `pot_odds_call` when `aggression_score >= 4.0`. Fold instead,
whatever the price says.

**Why the price test is not enough.** `equity >= pot_odds + call_margin` consumes
`estimate_equity`'s number at face value. §12 measured that number as +0.081
optimistic in aggregate but **−0.181 at aggression 4–6** — inflated in exactly
this band, with no mechanism for the pot-odds test to discount it.

**Pre-registered, not fitted.** §18's bar was recorded BEFORE the data: build at
>5:1 losing:winning hands, park at ~3:1. Sequence: 3.33:1 (n=1046) → 4.33:1 →
5.00:1 (at the bar, held for tiebreak per explicit instruction) → **5.33:1 on
2026-09-10**, cleared. The tiebreaking hand carried −2,939 on its own, more than
the entire prior bucket loss; the bar was specified on the ratio and was NOT
re-specified once that was visible.

**Scope, and why each limit is load-bearing.** Postflop only (preflop
`aggression_score` is absent/unreliable — two of the three `pot_odds_call` sites
are preflop and the rule must not reach them); the `pot_odds_call` arm only;
placed AFTER the raise arm so it only ever converts a call into a fold;
unreachable at `to_call == 0` because the enclosing block is the `to_call > 0`
else. `stalled_guard` is tested first and keeps precedence. `stack_risk_cap`
runs downstream, so an earlier fold should REDUCE its fire rate.

**Threshold 4.0, no made-hand exemption.** Tightening to 5+ shrinks the
population with no evidence for the narrower cut. The bucket's winners are 3
hands at +190 — too few to carve out without fitting to noise. If production
shows the rule folding too much, add the exemption THEN, with its own bar.

**Archive replay at ship time.** 57 decisions flip call→fold across 53 hands; 21
attributable, 17 losing (−5,864) and 4 winning (+243). NOTE the replay population
is a strict SUPERSET of the §18 bucket (19 hands): the bucket uses
largest-commitment attribution, the rule fires per decision. All 19 bucket hands
are covered; the 2 extra carry −391. **−5,621 is exposure removed, not chips
saved** — folding earlier changes the hand.

**Watch after enabling.** Primary: `pot_odds_call` net attribution (−3,035 over
107 hands at ship). Secondary: the agg>=4 ratio — if the rule works, `win_n`
stays 3 and `lose_n` stops growing. Risk: a bust spike would mean it folds too
much, and that is the signal for the made-hand exemption or a tighter threshold.

## §20 SEASON BOUNDARY — S18 → S19 (2026-09-11)

**S18 ended 09:14** (`join -> 409 competition_not_active`). Final: rank 216,
best 11, score 333, 3,640 hands, 6 rebuys. **S19 opened 13:11 UTC**, competition
`cmtwz5y2z2u4k13ubquaqff8w`, verified free before joining (`entryFeeMon: 0`,
`inviteOnly: false`, `xAuthRequired: false`).

**NUMBERS DO NOT POOL ACROSS THIS LINE.** Different field, different pool, fresh
bankroll and rebuy counter. Any chip-denominated statistic that spans it is
meaningless — as are PFR/VPIP/call:raise comparisons, which are pool-dependent.
Only mechanism checks transfer. The per-run `=== COMPETITION` banner marks the
split in every log.

**S18 CLOSING BASELINE** (carried for comparison only, never for pooling):

    attributable hands 2,168        net -3,294
    pot_odds_call      215 hands    net -8,301   mean -38.6
    aggression >= 4    lose_n 17, win_n 3        ratio 5.67:1
    AF 1.232   VPIP 0.361   PFR 0.186   decision rate ~146/run

**§19 SHIPPED BUT UNTESTED.** `AGGRESSION_FOLD_ENABLED` went live at v123 and
fired **3 times** across v123–v124 (~280 decisions) before the season ended.
Three firings cannot move a 215-hand attribution, and the 17th losing hand in
the bucket entered from pre-rule play. So S18's closing numbers are NOT evidence
for or against §19 — its first real test is S19's clean data.

**S19 config** (unchanged from S18's close): `PREFLOP_POLARIZE_ENABLED` true,
`SIZING_SCHEME_ENABLED` true, `AGGRESSION_FOLD_ENABLED` true,
`FOLD_EQUITY_ENABLED` false.

**Primary signals, in order:** (1) `aggression_fold` firing count and
`pot_odds_call` net attribution; (2) whether the `aggression >= 4` bucket stops
accumulating losses now the rule is live; (3) standard per-run chips/rank/hands/
rebuys/decision rate/AF-VPIP-PFR.

**S19 STARTING BASELINE** (captured at join, 2026-09-11 21:03, v126):

    sampleSize 0   vpip/pfr/af/wtsd/wsd all null   playingStyle null
    chips 1,000    rebuyCount 0 (max 99,999)       rank 53   score 1,000
    submissions 0

Everything is zero/null by construction — that IS the baseline. Any S19 statistic
is measured from here, never from S18's closing numbers above.

## §20 Flop no-pair call discount — SHADOW ONLY (added Oct 7, 2026)
Archive audit: flop `pot_odds_call` with made_category 0 = 262 hands, -4,379 chips (whole-hand net), 14% showdown wins vs 0.3-0.5 estimated equity.
Rule (shadow): on the flop with no pair, would call only with a flush draw / OESD or if 0.6*equity >= pot_odds + call_margin; else would fold.
In-sample backtest: would-fold set 134 hands, -2,692, 3% showdown wins; kept-call set 128 hands, -1,687, 26%.
In-sample, so not proof. Logged live to `shadow_flop_nopair.jsonl` (decision + settled rows joined on hand_id). Enforce only after several hundred resolved live rows confirm the would-fold set loses. Backup of pre-change agent: `live_agent_backup_pre_flopshadow.py`. Tests: `test_flop_nopair_shadow.py`.

## §21 AGGRESSION_FOLD THRESHOLD 4.0 → 3.0 — SHIPPED 2026-10-07

**Evidence.** The 4+ band is now EMPTY of `pot_odds_call` hands (§19 refuses
them), and the remaining postflop loss rises monotonically with opponent
aggression, measured on `chipDelta` across S19 runs 126–174:

    agg 1-2:  36 hands,  -216   (-6.0/hand)
    agg 2-3: 134 hands, -1975  (-14.7/hand)
    agg 3-4:  28 hands,  -812  (-29.0/hand)

This is the §12 equity-calibration curve expressed in MONEY rather than in
equity estimates — two independent methods agreeing. 3.0 takes the worst
remaining band and leaves the near-break-even 1–2 band alone.

**NOT 2.0 yet.** 2.0 would capture 162 hands and ~90% of the remaining loss,
but it is a large behavioural change that would land on top of this one and
destroy the attribution that produced the gradient above. One change at a time
is the only reason that gradient was visible.

**PRE-REGISTERED BAR — written BEFORE the data arrives.**
Measurement window: the next **≥400 new S19 attributable hands** (baseline
2,720) **and ≥20 new `aggression_fold` firings** (baseline 62). Baselines at
ship: `pot_odds_call` 230 hands / −3,322 = **−14.4/hand**; WSD **0.381**;
rebuys 5.

  - **Advance to 2.0** if `pot_odds_call` mean/hand improves to better than
    −11.0 AND WSD ≥ 0.40.
  - **Revert to 4.0** if `pot_odds_call` mean/hand worsens beyond −18.0, OR
    ≥3 new rebuys land inside the window.
  - **Otherwise hold** and keep measuring.

Report which of the three happened. Do NOT re-specify the bar once the data is
in, and do not substitute a different framing for whichever outcome lands.

**CROSS-TRACK INTERACTION WITH §20 (verified, not assumed).** §20's flop
no-pair rule is genuinely action-neutral: it sits INSIDE the `pot_odds_call`
branch *after* `action` is already assigned to "call", only records, and is
wrapped in try/except. So it confounds nothing today. But §19 is tested
**before** it, so any hand §19 refuses never reaches §20's logger. Measured
cost of today's change to §20's sample: **9 of 150 flop no-pair hands (6%)** —
0 at threshold 4.0, 9 at 3.0. Small, but if §19 ever reaches 2.0 the two
populations overlap heavily and §20's sample must be re-checked first.

**Also shipped 2026-10-07:** join-path rebuy. A run that STARTS on a
sub-big-blind stack now calls `/texas/rebuy` instead of retrying a join that
cannot succeed (v170/v171 each burned five retries to zero decisions and drove
the chain to 2/3 on its terminal condition). Capped at one rebuy per launch;
402 still refuses to pay. Tests: `test_join_rebuy.py`.

**TWO SESSIONS ARE EDITING THIS AGENT.** §20 and §21 were shipped the same day
from different sessions, neither aware of the other until the backlog was read.
Before any further strategy change, check what the other track has in flight —
`grep -nE "^[A-Z_]+ = (True|False|[0-9.]+)" live_agent.py` lists the live flags
and is the fastest way to see it.

## §22 ACTION-CONDITIONED RANGES — PROPOSED, SHADOW-FIRST (added 2026-10-08)

**Status: SHADOW LOGGER SHIPPED 2026-10-08 (`S22_SHADOW_ENABLED`). Decisions unchanged.**
`strategy.estimate_equity_ranged` + `live_agent.shadow_record_s22` ->
`shadow_equity_ranged.jsonl`, one row per postflop decision facing a bet
(equity, equity_ranged, accept_rate, opp_actions, hand_id). Joined to outcome
via hand_archive.jsonl. Tests: `test_s22_shadow.py` (6/6). Pre-change copies:
`backups/live_agent_v188_pre_s22.py`, `backups/strategy_pre_s22.py`.
Note: the fit and the shadow sample must not overlap when judging the bar -
only rows logged AFTER 2026-10-08 count.

### The finding that makes this buildable now
`hand_archive.jsonl` holds hole cards for EVERY seat in all 7,786 hands, not
just showdowns. So "what does an opponent hold when they take action X on
street Y" can be measured directly, with no showdown selection bias.
Script: `analysis/fit_action_ranges.py`. Opponents only (our seat excluded),
hand class = their made hand on the board as it stood at that action:

    street action   n     air   pair  two_pair+
    Flop   check    841   0.64  0.30  0.05
    Flop   call     205   0.21  0.68  0.11
    Flop   bet      838   0.25  0.61  0.13
    Flop   raise     34   0.00  0.38  0.62
    Turn   check    244   0.41  0.44  0.15
    Turn   bet      230   0.17  0.53  0.31

### What it says about the current model
`range_strength` only ever does one thing: reject *air* trials, with
probability capped at 0.70. It never shifts weight from pair to two-pair+.
The data says that misses the important part: a flop raise was **0% air and
62% two-pair-or-better** (n=34, small). A flop bet-plus-raise scores ~3.0 ->
range_strength ~0.44, so the sim still lets roughly half of the air through
and samples two-pair+ at roughly its random-hand rate. This is the same gap
§1's conclusion named ("action-conditioned ranges, not strength
percentiles"), and the same shape as `cmtd2ab0`.

### Proposed design (Loki-style weight table, U. Alberta 1999)
Per opponent per hand, a weight on each class (air / pair / two_pair+),
starting from the random-hand prior on the current board, multiplied by
P(class | street, action) from the table above on every action they take.
Sample opponent hands in proportion to those weights. Multiplicative, so
evidence accumulates across actions and streets (Principle 1) and never
resets at street boundaries.

### Constraints this must respect
- **§13:** the new equity is a SEPARATE number (`equity_ranged`), logged next
  to `equity`. No existing consumer reads it. Thresholds tuned on the old
  number stay untouched.
- **Principle 11:** these rates are from this arena's population over the
  archive's dates. Refit before trusting them after a season change.
- **Raise n=34** — too thin to tune on. River rows are missing entirely.
  The shadow period should grow these counts before anything is wired in.

### Pre-registered bar (set BEFORE looking at shadow results)
Ship to a consumer only if, over >= 300 shadow-logged postflop decisions
facing a bet or raise, `equity_ranged` has lower Brier score against the
actual win/loss than `equity`, AND the calls it would have flipped to folds
have negative summed chipDelta. Either condition failing = do not ship.

## §23 SAFEGUARD FOLD COUNTERFACTUAL — the brake is not the leak (2026-10-10)
Script: `analysis/brake_counterfactual.py`. For every velocity_brake /
stack_risk_cap FOLD facing a bet (136), looked up all hole cards + board in
hand_archive and computed calling-and-checking-down vs folding
(eq*pot - (1-eq)*to_call; assumes no further betting).

    path            season  n   fold right  true eq  modeled eq  call-instead gain
    velocity_brake  pre-S19 14  11/14       0.19     0.56        -3,494
    velocity_brake  S19     12  10/12       0.16     0.71        +1,612 *
    stack_risk_cap  pre-S19 43  33/43       0.18     0.54       -10,911
    stack_risk_cap  S19     67  39/67       0.28     0.54        +4,893 *

    * S19 totals are driven by 3 hands with pots of 113,116 / 17,785 / 8,586
      chips - side-pot/all-in spots the formula can't price (we could not win
      the whole pot). Excluding them: brake S19 ~ -1,068, cap S19 ~ -2,793,
      i.e. the folds SAVED chips in both seasons.

Conclusions:
- The -119/hand on velocity_brake (and -37 on stack_risk_cap) is SUNK cost:
  chips committed before the safeguard fired. The folds themselves were right
  ~80% of the time and saved ~18k chips across both seasons. Do not loosen.
- The real signal: at the moment these safeguards fire, MODELED equity is
  0.54-0.71 and TRUE equity is 0.16-0.28. The bot is walking into big pots
  holding a hand it overrates by ~0.4. That is an upstream equity problem,
  exactly §22's target - strengthens the case for §22, wired into the
  consumers that commit chips early (pot_odds_call, value_raise), not here.
- Not covered: brake DOWNGRADES (bet->check, raise->call), 66 rows; needs a
  different counterfactual.

## §24 BEAT-RATE LOOKUP — SHADOW SHIPPED 2026-10-10 (`S24_SHADOW_ENABLED`)
Why: §23 showed modeled equity ~0.59 vs true ~0.20 at brake/cap spots; §22
only moves that to 0.54 (it ignores bet size and lumps all pairs together).

What: count it directly. `analysis/fit_beat_rates.py` walks every postflop
bet/raise/call in hand_archive (pot rebuilt from amounts) and, for every
other live player, records whether the actor was AHEAD of them, keyed by
street | actor's size bucket (small <0.5 pot, med <=1, big >1, raise) | the
other player's class (air / pair_low / pair_top / two_pair / trips+).
Output `analysis/beat_rates.json`. Sizing matters a lot, e.g. Flop:
pair_top vs small bet behind 7%, vs med 23%; pair_low vs big bet behind 94%.

Held-out test (fit EXCLUDING all 137 §23 hands; 35 of them had a readable
aggressor bet and a populated row):
    bot's equity 0.56 | §24 ahead-prob 0.27 | true equity vs aggressor 0.23
    mean abs error: bot 0.35, §24 0.20

Shadow: `live_agent.shadow_record_s24` -> `shadow_beat_rate.jsonl`, every
postflop decision facing a bet with a readable aggressor. Uses real_hand_id.
Decisions unchanged. Backups: `backups/*_pre_s24.py`. Test: test_s24_shadow.py.
Live table is refit on ALL archive hands (incl. §23); re-fit periodically.

Known limits: turn/river rows are thin (flop fallback used); raise rows thin
(falls back to 'big'); "ahead now" is not showdown equity - draws are ignored.

PRE-REGISTERED BAR (set 2026-10-10, before any shadow rows exist). Use only
rows logged after 2026-10-10. With >= 150 rows whose hand is in hand_archive:
 1. Target = true equity vs the logged aggressor (both hole cards known,
    runout sampled). §24 ahead_prob must have lower mean abs error than
    `equity`.
 2. Rows where the bot called/raised but ahead_prob < pot_odds must have
    negative summed chipDelta.
Both pass -> propose wiring into pot_odds_call ONLY, with its threshold
re-derived (§13). Either fails -> do not ship.

Note for test_shadow_logger.py: fails both before and after §24 - its
synthetic ids are now rejected by real_hand_id (2026-10-08 guard). Needs its
fixture ids updated to cuid shape, not a code change.

### Shadow writes are OPT-IN (2026-10-10) — read before writing any replay/backfill
All shadow loggers (§19, §20, §22, §24, incl. settlement halves) write only
when `live_agent.LIVE` is True, which ONLY `main()` sets. Importing
live_agent and calling `decide_live` directly (tests, replays, backfills)
writes NOTHING. A replay that genuinely needs shadow output must redirect
every *_PATH to a scratch file FIRST, then set `LIVE = True` — never the
other way round (that order is how the logs were polluted four times).
Any NEW shadow logger must call `shadow_writes_allowed()` before writing.
