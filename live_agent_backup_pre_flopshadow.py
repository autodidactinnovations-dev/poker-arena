"""
DevFun Arena - live Texas Hold'em polling agent for the free Playground
competition (matchmaking lobby, not the sandbox bundle flow used by Eval S1).
Currently targets Playground S16 (S15 ended; see COMPETITION_ID below) - the
API contract (introspection re-checked 2026-08-26) is unchanged from S15:
/texas/pending-actions and /texas/action are byte-for-byte identical, and
S16's rules now advertise free, unlimited rebuys on bust (vs. S15, where the
docs promised free rebuys the API never actually granted - see the round-51
postmortem below and bug ticket cmt6zkvt9fgypxjsh5mas7dvk). Re-verify that
ticket's resolution before relying on rebuy-on-bust; the stack-risk safeguards
below stay on regardless since they're cheap insurance either way.

Reuses the equity engine and real-schema table/seat parsing already proven
against Eval S1's identical table shape (see strategy.py), but targets the
live /texas/action contract directly:
  - action enum is ["fold","check","call","bet","raise","all-in"] (hyphenated
    all-in, unlike the sandbox flag name canAllIn).
  - message is REQUIRED chat text (1-500 chars).
  - reasoning is required only when allowedActions.reasoningRequired is true,
    and must be a single-line YAML flow-style string, e.g.
    {ke: "62% eq", sr: "pot odds 25%, eq edge"}. Fields are omitted rather
    than fabricated when we don't have a real basis for them (no range
    modeling is implemented here, so `vr`/`bf`/`pp` are left out).

Post-mortem on rounds 27-36 (flat score stretch, analyzed via /texas/recent-tables
hole-card reveals): v6 was folding top-15% hands like AJs/AJo to a single raise
at a 6-max table, because num_opponents counted every non-folded seat -
including seats that haven't acted yet this street and mostly fold before
showdown - as a certain, full-strength random opponent. Fixed by splitting
active seats into "certain" (already acted this street, via recentEvents)
vs. "pending" (not yet acted, included only probabilistically in the equity
sim - see strategy.py's estimate_equity/`_acted_seat_numbers`). Repro:
AJs facing a 3x raise with 5 active-but-mostly-unacted seats now correctly
calls instead of folding.

v7: after that fix started winning more pots, round 51 busted the entire
1916-chip bankroll in one hand - three straight turn re-raises, each
"justified" by a static equity read in the 72-77% range, with zero range
modeling to notice that a repeated re-raise is itself strong evidence we're
beat. Playground S15 has no rebuys once busted, so this was a full, permanent
wipeout on this agent (see the bug report re: rules promising free rebuys
that the API doesn't honor - ticket cmt6zkvt9fgypxjsh5mas7dvk). v7 adds two
independent safeguards, both scoped to this live agent only (strategy.py's
own decide(), used for the separately-scored Eval S1 flow, is untouched):
  - Escalating re-raise wariness (`_opponent_raises_this_street`,
    `escalation_bump`): each opponent raise on a street beyond the first
    bumps every continue/re-raise threshold by +0.10, so a raise war
    requires increasing equity to keep matching instead of being fought on
    a flat threshold.
  - A hard stack-risk cap (`STACK_RISK_CAP`, `STACK_RISK_EQUITY_FLOOR`):
    never commit more than half the current stack in one decision unless
    equity clears 80%, regardless of what the threshold logic above
    concluded. Pure circuit breaker against equity-model error.
Verified against a direct repro of the round-51 shape (see git history /
session notes) - the same spot now folds instead of going bust, while a
genuinely dominant hand (e.g. a set) still correctly gets it all in.
"""

import json
import ssl
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

sys.path.insert(0, "C:/Users/billy/poker-arena")
import strategy as S

# TLS 1.3 handshakes to arena.dev.fun's Cloudflare edge get reset by this
# host's OpenSSL/Schannel stack (openssl s_client over TLS1.2 succeeds fine).
# Capping at TLS 1.2 avoids the ConnectionResetError this otherwise causes on
# every request.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.maximum_version = ssl.TLSVersion.TLSv1_2

BASE = "https://arena.dev.fun/api/arena"
CREDS_PATH = "C:/Users/billy/poker-arena/.arena-credentials"
COMPETITION_ID = "cmtwz5y2z2u4k13ubquaqff8w"  # [Poker] Playground S19 (from v126)
# S18 ENDED 2026-09-11 09:14 (join -> 409 competition_not_active). Final: rank
# 216, best 11, 3640 hands, 6 rebuys. S19 opened 13:11 UTC the same day and is
# free (entryFeeMon 0, not invite-only, no X auth) - verified before joining.
# DO NOT POOL S18 AND S19 NUMBERS. Different pool, different field, fresh
# bankroll and rebuy counter. Every chip-denominated statistic restarts here;
# see the "=== COMPETITION" banner each run writes for the split point.
# v39: back to Playground S18 for a REBUY RUN. We never left the S18 table when
# v37 was stopped - the arena kept taking timeout actions for us, so the seat
# stayed live and drifted 2 -> 5 chips over 6 hands with no agent attached.
#
# At 5 chips against a 2-chip big blind we are 2.5bb. The point of this run is
# NOT to play well: it is to spend the remainder, so total chips drop BELOW the
# big blind, chipState flips to "busted", and the auto-rebuy restores a full
# 1000-chip stack in the live 6-max pool. That is the cheapest route to the only
# free venue that can actually exercise the Stage 1 mechanisms - S17 costs an
# irreversible buy-in and S1's attempt cost is unknown.
#
# The rebuy path is UNVERIFIED: rebuyCount has been 0 on every run this project
# has done, so this run is also the first test of it.
#
# Benchmark competitions (Eval S1, seed_poker_eval_s1) are entered via
# /texas/benchmark/start instead; support is built and parked, see backlog §10.
#
# S1 is PVE: a fixed number of reset-stack hands against a FIXED REFERENCE
# PANEL, not a live 6-max pool. Its numbers are NOT comparable to S18's and must
# not be pooled with them:
#   - PFR, VPIP, call:raise and the branch B/C mix all depend on what the
#     OPPONENTS do. A fixed panel is a different population (Principle 11).
#   - S18 hands were played at 2-146 chips against 950-2150 chip opponents;
#     S1 resets the stack every hand.
# Only the MECHANISM checks transfer, being properties of our own code rather
# than of the pool: does value_3bet_tier fire, does the escalation cap block at
# opp_raises >= 2, does the brake fall through to call rather than fold.
#
# Why not the alternatives: Eval Open S4 is PAID (per-run USD-T0 on X Layer)
# and heads-up against a bot - never join it without an explicit human decision
# to spend money. Tournament S17 is a single irreversible buy-in with no
# rebuys, worth spending on VERIFIED code rather than on a mechanism check.
# Season history: S15 ended 2026-08-24. S16 (cmt78jcn6c9a1ddft3z2to1y5) ended
# 2026-08-29 09:57 UTC-4, MID-RUN, stranding 245 chips - ended competitions have
# no settle/withdraw endpoint (checked: nothing in the API surface does this),
# so a bankroll left in a finished season is simply gone. Seasons appear to turn
# over every few days; expect this constant to need updating again.


# --- Join outcome classification -------------------------------------------
# The API documents NO error enum for /texas/join, so these strings are only
# discoverable by hitting them. That makes a blacklist of "known-bad" errors
# structurally unsafe: any unrecognised failure defaults to retry forever.
# That is exactly how one run burned 3,087 consecutive re-join calls against a
# competition that had ENDED two hours earlier (competition_not_active), and
# an earlier one burned a whole session on insufficient_chips.
#
# So: whitelist what is known-recoverable, treat everything else as terminal.
# A new unknown error now stops the run cleanly instead of hammering.
JOIN_SEATED_ERRORS = {"already_queued", "max_concurrent_tables"}
JOIN_REBUY_ERRORS = {"insufficient_chips"}


# Competitions that cost real money to enter. Joining one is a financial
# transaction and is never an automatic decision - it requires a human choosing
# to spend. Eval Open S4 charges per run in USD-T0 on X Layer and pays out from
# an entry pool. This list exists because S4 was once proposed as a venue purely
# on the basis that /texas/rebuy-status said "Agent has not joined yet" - the
# join-state endpoint says nothing about cost, and the competition description
# is the only place the payment is stated.
PAID_COMPETITIONS = {
    "cmsy5imvg2h6zeczzzw1fc7ya": "[Poker] Eval Open S4 - per-run fee in USD-T0 on X Layer",
}

# Human-readable names for the run-start banner. Only for logging - the entry
# path itself is derived from the arena's own response, never from this table.
COMPETITION_LABELS = {
    "cmtwz5y2z2u4k13ubquaqff8w": "[Poker] Playground S19 (live 6-max pool)",
    "cmtlk9djt0mtz7njb1lf85704": "[Poker] Playground S18 (live 6-max pool)",
    "cmtlk922v0mtf7njb5z4rym96": "[Poker] Tournament S17 (live 6-max, single buy-in)",
    "seed_poker_eval_s1": "[Poker] Eval S1 (PVE, fixed reference panel)",
    "cmsy5imvg2h6zeczzzw1fc7ya": "[Poker] Eval Open S4 (PAID, heads-up vs bot)",
}


def classify_join(status, resp):
    """-> one of: ok | seated | rebuy | retry | terminal."""
    if status == 200:
        return "ok"
    # 402 anywhere in the join path means the arena wants payment. This bot
    # never pays: it carries no txHash or paymentReference and stops instead.
    if status == 402:
        return "terminal"
    err = (resp or {}).get("error")
    if status == 409 and err in JOIN_SEATED_ERRORS:
        return "seated"
    if err in JOIN_REBUY_ERRORS:
        return "rebuy"
    # Transport failures (-1) and server-side faults are genuinely transient.
    # 429 is a throttle: back off rather than give up.
    if status == -1 or status == 429 or status >= 500:
        return "retry"
    return "terminal"


def load_api_key():
    with open(CREDS_PATH) as f:
        return json.load(f)["apiKey"]


API_KEY = load_api_key()


def call(method, path, body=None, query=None):
    url = BASE + path
    if query:
        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in query.items())
        url += "?" + qs
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("x-arena-api-key", API_KEY)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return -1, {"error": str(e)}


def build_reasoning(equity, pot_odds, action):
    parts = [f'ke: "{equity * 100:.0f}% eq"']
    if action in ("bet", "raise", "all-in"):
        parts.append(f'sr: "pot odds {pot_odds * 100:.0f}%, equity edge"')
    return "{" + ", ".join(parts) + "}"


# /texas/agent-stats is real, stable, cross-session opponent data - a fix for
# the "small wins, big losses" pattern (max win +17 vs losses of -193/-249,
# both from legitimate equity-justified calls that just lost at showdown to a
# bet from a specific opponent we had zero read on). The in-process VPIP/AF
# tracker (_update_opponent_model/_opp_profile) resets every ~7-minute
# process and rarely accumulates enough hands to say anything; this doesn't.
# The endpoint's own docs say it's "stable enough to fetch once per opponent
# and reuse rather than polling" - cached to disk so it survives across the
# many short-lived live_agent.py processes a single competition run through.
OPP_STATS_CACHE_PATH = "C:/Users/billy/poker-arena/opponent_stats_cache.json"
OPP_STATS_FRESH_TTL = 6 * 3600
OPP_STATS_RETRY_TTL = 30 * 60


def _load_opp_stats_cache():
    try:
        with open(OPP_STATS_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_opp_stats_cache(cache):
    try:
        with open(OPP_STATS_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


_OPP_STATS_CACHE = _load_opp_stats_cache()


def get_opponent_stats(agent_id):
    entry = _OPP_STATS_CACHE.get(agent_id)
    now = time.time()
    if entry is not None:
        stats, fetched_at = entry.get("stats"), entry.get("fetched_at", 0)
        ttl = OPP_STATS_FRESH_TTL if stats and stats.get("playingStyle") else OPP_STATS_RETRY_TTL
        if now - fetched_at < ttl:
            return stats
    status, resp = call("GET", "/texas/agent-stats", query={"competitionId": COMPETITION_ID, "agentId": agent_id})
    stats = resp if status == 200 else None
    _OPP_STATS_CACHE[agent_id] = {"stats": stats, "fetched_at": now}
    _save_opp_stats_cache(_OPP_STATS_CACHE)
    return stats


# How likely a not-yet-acted-this-street opponent is to still be in the hand
# when it matters. Preflop, a seat that hasn't acted yet genuinely might fold
# to a raise (~35% is a reasonable continue-rate guess). Postflop, "pending"
# just means it isn't their turn yet - they already called through however
# many prior streets to get here, so they're much more likely to see this
# street through too. A flat 0.35 on every street caused v6 to keep chasing
# busted hands (e.g. A6s with no pair by the river) because it kept
# discounting players who'd already committed several streets of chips as if
# they were as foldable as a preflop limper. Untuned guesses, same as the
# 0.35 preflop baseline - a real fix would derive these from per-opponent
# WTSD/AF via /texas/agent-stats instead (see strategy.py's TODO).
_PENDING_WEIGHT_BY_STREET = {"Preflop": 0.35, "Flop": 0.65, "Turn": 0.80, "River": 0.90}

# Hard circuit breaker, independent of the equity model entirely: never let a
# single decision commit more than STACK_RISK_CAP of the current stack unless
# equity clears STACK_RISK_EQUITY_FLOOR. No range modeling exists in this
# strategy (see module docstring), so a self-computed 70-80% equity read
# should never be trusted enough on its own to justify a full stack-off -
# that's exactly what busted the whole 1916-chip bankroll in round 51 (three
# straight turn re-raises, each "justified" by equity in the 72-77% range).
STACK_RISK_CAP = 0.5
STACK_RISK_EQUITY_FLOOR = 0.80

# --- Velocity brake (SHADOW MODE - evaluated and logged, NOT enforced) -------
# Hand cmtcarsk busted a full 1000-chip stack at 0.828 equity: commitment went
# 0.105 -> 0.516 -> 1.000 across two turn decisions while equity barely moved,
# and BOTH existing safeguards missed it (escalation_bump needs 2+ raises on one
# street and saw only 1; the stack-risk cap needs equity < 0.80 and saw 0.828).
# The brake below triggers on how FAST commitment moves in a single decision,
# not on the equity level.
#
# Thresholds are per-street, not flat. Measured against 37 real outcome-matched
# hands, a flat 0.30 cap fired on 4 hands - 3 of which the agent WON (+32 chips
# given up to save 2). Preflop jumps are naturally large when short-stacked
# (observed max 0.40) while river jumps top out around 0.25, so a flat threshold
# is effectively preflop-only, i.e. backwards. The per-street values below fired
# on 2 of the same 37 hands, both LOSSES (-83 and -65), and zero winners.
STACK_VELOCITY_SHADOW_MODE = False  # ENFORCING (was shadow-only through hand cmtd2ab0)
STACK_VELOCITY_CAP_BY_STREET = {"Preflop": 0.45, "Flop": 0.35, "Turn": 0.25, "River": 0.20}
# NO equity override. There was one (0.95), and it is exactly what let hand
# cmtd2ab0 stack off 0.807 of the bankroll untouched: equity read 0.954 against
# `Ac 5s` on `Ad Ah 2c Qd 8s` - trip aces with a FIVE kicker, drawing dead to
# the villain's `Ts As`. The Monte Carlo is measuring strength against random
# hands, so it is most overconfident precisely when an opponent's repeated
# raises prove their hand is not random. A self-computed equity number is
# therefore not evidence at high commitment, no matter how high it reads, and
# must not be able to buy its way past this brake.
STACK_VELOCITY_MIN_BB = 20  # skip entirely on short stacks, where every call is
                            # a large fraction and folding just blinds us out
# The cap tightens as cumulative opponent aggression rises. This replaced a
# flat `if opp_raises_this_street >= 2: cap -= 0.05`, which had two defects:
# it read the per-street counter that resets at street boundaries (the same
# bug already fixed in range_strength), and it applied an identical decrement
# whether aggression was barely present or extreme.
#
# Cost of that on hand cmtfd8a3: a three-step turn war drove aggression to
# 9.5, range_strength correctly reached 0.67 - and the brake still passed the
# fatal 250-chip call at a jump of 0.184 against a 0.20 cap, under by 0.016.
# 258 chips.
#
# Curve choice matters and was fitted against real hands, not picked by feel.
# A Hill function is flat at low aggression and steepens later, which is the
# shape needed: an exponential decay of the same strength tightened the flop
# cap to 0.304 and would have braked a REAL WINNER (the +24 flop raise, jump
# 0.311). The Hill curve leaves that at 0.344 while still pulling the
# cmtfd8a3 cap down to 0.134.
STACK_VELOCITY_TIGHTEN_MAX = 0.65  # cap can shrink to 35% of its base at most
STACK_VELOCITY_TIGHTEN_K = 6.0     # aggression at which half of max tightening applies


def velocity_cap_for(street, aggression_score):
    """Base per-street cap, tightened by cumulative aggression."""
    base = STACK_VELOCITY_CAP_BY_STREET.get(street, 0.30)
    a = max(0.0, aggression_score or 0.0)
    k = STACK_VELOCITY_TIGHTEN_K
    tighten = STACK_VELOCITY_TIGHTEN_MAX * (a * a) / (a * a + k * k)
    return base * (1.0 - tighten)


# Made-hand category at or above which the brake is skipped ENTIRELY.
# 4 == straight (see _hand_rank_5: 0 high, 1 pair, 2 two pair, 3 trips,
# 4 straight, 5 flush, 6 full house, 7 quads, 8 straight flush).
#
# The line sits here because of DOMINATION RISK, not because 4 is a nicer
# number than 5. Categories 0-3 (high card, pair, two pair, trips) are
# routinely beaten by the SAME category with a better kicker - that is a
# common, silent, hard-to-detect way to be behind while holding what looks
# like a strong hand. It is exactly what cost 1000 chips on cmtd2ab0
# (A A A Q 8 losing to A A A Q T) and 83 on cmtb7bys (Q Q 8 losing to
# Q Q T). Those hands SHOULD stay braked.
#
# Categories 4+ have no kicker to be outrun by. They are not literally
# unbeatable within their category - a higher straight or higher flush
# exists - but that requires the opponent to hold specific, much rarer
# cards, rather than merely a better side card off the same pair. The
# frequent, invisible failure mode is gone.
#
# Bar was originally set at 5, which put the boundary inside an UNTESTED gap:
# the two validating hands were a flush (exempt) and trips (braked), and no
# straight was ever checked. A made queen-high straight (8c Jh on
# 7s Tc Qd 9c) was then folded on the river at 0.962 equity for -352 chips.
# See PRINCIPLES.md #9.
#
# The brake exists to distrust a self-computed equity estimate when commitment
# spikes. That distrust is about OPPONENT uncertainty - we are guessing what
# they hold. Our own made hand is not a guess: it is read directly off the
# board. Nothing in the brake distinguished the two, so it applied the same
# skepticism to a deterministic fact.
#
# Cost of that on hand cmtg76xo: we held Ah 2h on 7h 5h 4s 4h - a COMPLETED
# ace-high flush - and the brake folded it at a 0.803 jump against a 0.183
# cap. Villain had trip fours. 377 chips, handed over on a hand we could read
# exactly.
#
# Drawing hands stay fully subject to the brake - cmtg5d71 (pair + open-ended
# draw) still folds, and trips still folds, which is what correctly caught
# cmtd2ab0's dominated trip aces for -1000.
# --- Fold-equity bet engine (Stage 2, 2026-09-02) ----------------------------
# The strategy modelled ZERO fold equity: a bet was only ever justified by
# showdown equity clearing value_thresh. It could therefore never output a
# profitable bluff or semi-bluff, and - critically - no static backtest could
# ever justify adding one, because a replay holds opponents' actions fixed and
# cannot credit the pot a fold would have won. That is a permanent property of
# the validation method, and it is why PR1 and PR2 both backtested negative.
#
# P(fold) is MEASURED FROM THIS FIELD, not assumed. Across 172 classified
# postflop bets/raises in the archive (a bet counts as folded-to only if it was
# our last action in the hand AND the hand ended with us winning "Uncontested";
# it counts as called if we acted again or the hand reached showdown):
#
#     overall          P(fold) = 0.529   (91/172)
#     by street        Flop 0.559 (93) | Turn 0.404 (47) | River 0.625 (32)
#     by opponents     1 certain: 0.658 (76) | 2 certain: 0.333 (9)
#     by size          every bucket 0.40-1.00+ pot had P(fold) > alpha
#
# That last row is the finding: at EVERY size we have actually used, opponents
# fold more often than the break-even frequency alpha = bet/(pot+bet). Bluffing
# has been +EV in this field the whole time and the code could not express it.
#
# NOTE this contradicts the assumption that a loose-passive field folds less
# than a generic prior. It does not - at least not to OUR bets. Most likely
# because we bet rarely and selectively, so our bets are unusually credible.
# That is also the main reason for the haircut below.
P_FOLD_SINGLE = 0.55        # haircut from the measured 0.658 (n=76). Our bets
                            # are currently value-heavy and board-selected, so
                            # the measured rate may not transfer to a wider
                            # betting range. Deliberately conservative: a too-low
                            # prior means we skip marginal bluffs, which is the
                            # safe direction to be wrong in.
CALLED_EQUITY_HAIRCUT = 0.85  # equity_when_called < raw equity: a caller's range
                              # is stronger than a random continuing range.

# --- DOMAIN RESTRICTION (v2) -------------------------------------------------
# v1 of this applied P_FOLD_SINGLE to EVERY checking spot and consequently fired
# on 257 of 271 archived checks (95%), because the comparison reduced to
# `bet when eq < 1.38` - i.e. it could never output "check" at all.
#
# The cause was not the constant, it was its DOMAIN. 0.55 was measured on bets
# we actually chose to make: rare, value-heavy, board-selected. Applied to every
# spot it became a claim that opponents fold 55% to any bet anywhere, which is
# a biased estimator for the population of all POTENTIAL bets.
#
# The fix is to make the estimate's domain match its application - fire only in
# spots that resemble where 0.55 was measured:
FE_MIN_EQUITY = 0.25        # below this we hold nothing resembling a draw
FE_MAX_EQUITY = 0.55        # above this the normal value logic should handle it
FE_MAX_OPPONENTS = 1        # fold equity collapses multiway (measured: 0.658 at
                            # 1 opponent vs 0.333 at 2)
FE_EXCLUDE_STREETS = ("River",)   # no further streets to realize equity on, and
                                  # a river bluff is pure - no semi- about it

# EV_check was `equity * pot`, which treats checking as forfeiting every future
# street. That put a permanent thumb on the scale for betting in every single
# comparison. Checking retains the pot share AND the option to win later, so it
# is credited with a fraction of the remaining streets' value.
CHECK_FUTURE_VALUE = 0.35   # crude but directionally right: a checked flop still
                            # has turn and river to win; a checked turn has one.

# Minimum edge, as a fraction of pot, that EV_bet must beat EV_check by.
#
# Not a firing-rate knob. Both inputs above are uncertain to roughly +/-0.05:
# P_FOLD_SINGLE is measured with known selection bias (0.658 observed on bets we
# CHOSE to make, haircut to 0.55), and CHECK_FUTURE_VALUE is an unmeasured
# estimate. In the v2 replay many firings cleared by under 5% of pot - e.g.
# ev_bet 7.4 vs ev_check 6.8, or 25.7 vs 25.3. A decision that reverses when an
# input moves less than its own uncertainty is not a decision the model is
# making; it is noise wearing a decision's clothes. Require an edge larger than
# the inputs' uncertainty, or do not act.
MIN_EDGE = 0.05

# Master switch for the whole fold-equity path. Stage 2 is PARKED - see
# DESIGN_BACKLOG.md gap (C): 71% of firings flip when the inputs move 0.05
# against betting, so the model has no confident firings.
#
# This definition was accidentally deleted when the constants block above was
# rewritten, while line ~938 still referenced it. decide_live() wraps everything
# in try/except, so the resulting NameError would NOT have crashed - it would
# have silently dumped every postflop bet/check decision into the "playing it
# safe" fallback path. Caught before any run started.
FOLD_EQUITY_ENABLED = False

# === STAGE 1 (Option C): position-based RFI raising ==========================
# Ships WITHOUT backtest validation, deliberately. PR1 (raw-equity threshold)
# and PR2 (hand-class + position) both backtested negative, but a static replay
# cannot price fold equity, so it will always reject aggression. Judged on live
# agent-stats movement over several hundred hands, not on replay or one session.
#
# WHY OPTION C AND NOT PURE RAISE-OR-FOLD. Projected against 783 real dealt
# hands from the archive:
#     brief's tiers, raise-or-fold  -> VPIP 0.140  PFR 0.121  call:raise 0.2:1
#     widened tiers, raise-or-fold  -> VPIP 0.223  PFR 0.204  call:raise 0.1:1
#     THIS (RFI raise + keep calls) -> VPIP 0.556  PFR 0.213  call:raise 1.60:1
# Pure raise-or-fold folds everything outside the RFI range, collapsing VPIP to
# roughly half of v5's level - the exact regression that lost before - and
# overshooting the 1.5-1.7:1 target by an order of magnitude in the other
# direction. Our own field leaders are not doing it either: rank 1 runs VPIP
# 0.418 / PFR 0.244 = 0.71:1, i.e. a substantial calling range.
#
# So: RAISE the hands that should be raised, CALL the rest exactly as before.
# This moves PFR alone and leaves VPIP untouched, which is a cleaner test of
# "PFR is the discriminator, not VPIP" than a change that moves both.
STAGE1_RFI_ENABLED = True

# Iso-raise sizing, in big blinds. Pot-fraction sizing collapses preflop: the
# pot is 1.5bb before anyone acts, so even a 1.1x-pot raise is a ~3bb open no
# matter how many limpers are already in. Each limper adds a big blind of dead
# money AND one more hand to get through, so the open has to grow with them or
# it prices everybody in - which is how a "raise" turns into a bigger multiway
# pot with the same passive result.
ISO_RAISE_BASE_BB = 4.0
ISO_RAISE_PER_LIMPER_BB = 1.0

_RFI_RANK = {r: v for v, r in enumerate("23456789TJQKA", start=2)}


def _rfi_tier(hole_raw):
    """Position tier a hand qualifies for: 1 (any), 2 (HJ+), 3 (CO/BTN/SB), or
    None (never open). Pure card reading - no equity, no model input."""
    if not hole_raw or len(hole_raw) != 2:
        return None
    try:
        r1, s1 = _RFI_RANK[hole_raw[0][0]], hole_raw[0][1]
        r2, s2 = _RFI_RANK[hole_raw[1][0]], hole_raw[1][1]
    except (KeyError, IndexError):
        return None
    hi, lo, suited = max(r1, r2), min(r1, r2), s1 == s2
    pair, gap = hi == lo, hi - min(r1, r2) - 1
    # Tier 1 - open from any position
    if pair and hi >= 10:                                   return 1   # AA-TT
    if suited and hi == 14 and lo >= 11:                    return 1   # AJs+
    if not suited and hi == 14 and lo == 13:                return 1   # AKo
    if suited and hi == 13 and lo == 12:                    return 1   # KQs
    # Tier 2 - HJ and later
    if pair and 7 <= hi <= 9:                               return 2   # 99-77
    if suited and (hi, lo) in ((14, 10), (13, 11), (13, 10), (12, 11), (11, 10)):
        return 2                                                       # ATs KJs KTs QJs JTs
    if not suited and (hi, lo) in ((14, 12), (14, 11), (13, 12)):
        return 2                                                       # AQo AJo KQo
    # Tier 3 - CO/BTN/SB. Widened past the brief's list to hold VPIP in the
    # field's winning band; see the projection above.
    if pair:                                                return 3   # 66-22
    if suited and hi == 14:                                 return 3   # any suited ace
    if suited and hi >= 12:                                 return 3   # any suited K/Q
    if suited and hi >= 10 and lo >= 7:                      return 3
    if suited and gap <= 1 and lo >= 4:                      return 3   # 54s+ connectors
    if not suited and hi == 14 and lo >= 8:                  return 3   # A8o+
    if not suited and hi >= 12 and lo >= 10:                 return 3   # KTo QJo QTo
    if not suited and (hi, lo) == (13, 11):                  return 3   # KJo
    return None


# Widened one step at EARLY and HJ, 2026-09-03 (v35), against v34's measured
# branch-C distribution. NOT a looseness preference - the reason is structural:
#
# The table is 6-max in 1181 of 1186 logged preflop decisions (every
# position_frac is a multiple of 0.2), so these labels are correctly specified
# for the table we are actually at. What is skewed is BRANCH C, not our seating:
# per hand we sit EARLY/HJ 35.2% of the time (near the 33% a uniform 6-max
# rotation gives), but 71.4% of branch-C decisions come from EARLY/HJ. That is
# mechanical - from CO/BTN somebody has usually already raised, which routes the
# hand to branch B. Late position, where these tiers are widest, almost never
# REACHES the open branch. So the wide end of the table was doing little work.
#
# Measured effect on v34's 42 branch-C decisions: qualifying rate 11.9% -> 21.4%.
# All of the observed gain is HJ (4 hands unblocked, all tier 3); EARLY -> tier 2
# unblocked nothing in that sample, and only 4.7% of branch-C EARLY hands across
# all history are tier 2. Kept anyway as the deliberate one-step move, but it is
# the HJ change that should show up in the numbers.
_RFI_MAX_TIER = {"EARLY": 2, "HJ": 3, "CO": 3, "BTN": 3, "SB": 3, "BB": 3}

# --- PREFLOP POLARIZATION (v44) ----------------------------------------------
# Raise or fold preflop; stop calling. Targets AF, which the arena computes
# ACROSS ALL STREETS - our postflop AF is already 0.959, but 1141 of our 1579
# calls are preflop, so that is where the denominator lives. Converting every
# postflop call to a raise would still only reach AF 0.983; postflop cannot
# reach the target arithmetically.
#
# The field pattern is unambiguous - every high-AF agent has vpip ~= pfr:
#     af 62.0  vpip 0.219 pfr 0.219      af 9.6  vpip 0.888 pfr 0.882
#     af 49.1  vpip 0.377 pfr 0.374      af 8.6  vpip 0.174 pfr 0.166
#     us: af 0.49, vpip 0.520, pfr 0.181, labelled "loose-passive / fish"
# Our PFR is AT the field median (0.173). Our VPIP is nearly DOUBLE it (0.276).
# The whole gap is preflop calling, and 468 of those calls are with hands our
# own RFI table says are never worth opening.
#
# A: facing a raise, fold hands below tier 3 - if it is not good enough to
#    open, it is not good enough to call a raise. BB EXEMPT: folding a defended
#    big blind at a good price is its own leak, and the non-exempt variant
#    folds 98 more BBs at a median 0.286 price for +0.04 AF. Not worth it.
# B: branch C (limped / BB-only) becomes raise-or-fold - no calls at all.
#
# Projected over 1996 logged hands:
#     AF   0.433 -> 0.701      VPIP 0.549 -> 0.417     PFR 0.125 -> 0.163
# Most of the gain is FOLDING, so this REDUCES delta_frac exposure rather than
# adding it - the brake, stack_risk_cap and stalled_guard see less, not more.
#
# Shipped on theory, deliberately without a backtest gate: retrospective data
# cannot price aggression (a fold ends the hand, so the counterfactual is never
# observed). Judged live on stat movement over several hundred hands, not chips
# from one run. It may lose first.
PREFLOP_POLARIZE_ENABLED = True

# --- SIZING SCHEME + TURN/RIVER SEMI-BLUFFS (v46) ----------------------------
# Two coupled defects, shipped together because fixing either alone makes the
# other worse: fix size without frequency and there is nothing to bluff with;
# fix frequency without size and opponents get better-priced reads.
#
# DEFECT 1 - a monotonic SIZE TELL. Median bet-to-pot climbed with hand
# strength across seven categories: 0.46 air / 0.73 pair / 0.70 two pair /
# 0.79 trips / 0.80 straight / 0.88 flush / 0.85 boat. corr(size, category)
# = +0.210. The cause is in the source, not emergent: `sized_bet(VALUE_SIZE_HIGH
# if equity > 0.82 else 0.5)` sizes directly off equity.
#
# DEFECT 2 - ZERO turn/river bluffs across 124 turn and river bets. Overall
# value:bluff 6.56:1 against a 2-3:1 reference, while the bluffs that DO fire
# get through 70.6% of the time and make money. Underused, not risky.
#
# Size is a function of STREET and BOARD TEXTURE only - never of our hand.
# Same size for value and bluff on a given street; that is the whole point.
SIZE_BY_STREET = {"Flop": 0.66, "Turn": 0.50, "River": 0.40}
SIZE_TEXTURE_DRY = 0.85     # rainbow, unpaired, unconnected - range-favouring
SIZE_TEXTURE_WET = 1.15     # 3-flush, paired, or connected - draws exist
# Projected: corr(size, category) +0.210 -> -0.046. Residual is not a bug -
# wet boards genuinely make more hands AND get the larger multiplier, so the
# two correlate in the world. What matters is that size is unpredictable from
# OUR holding.
#
# Turn/river semi-bluffs, every condition load-bearing:
BLUFF_MIN_EQUITY = 0.40     # a real draw, not a hope. Of 36 selected spots,
                            # 30 are one-pair and 6 cat-0; equity 0.40-0.69.
BLUFF_MAX_OPPONENTS = 1     # heads-up ONLY. Fold equity collapses multiway and
                            # that is the genuinely reckless case.
BLUFF_MAX_AGGRESSION = 4.0  # never bluff into a raise war: calibration (12)
                            # measured our equity read at -0.181 over-confident
                            # in the 4-6 aggression band. Bluffing there means
                            # bluffing on a number known to be inflated.
BLUFF_MAX_COMMIT = 0.25     # a 40%-committed spot is not a semi-bluff. Excludes
                            # exactly the "escalate then abandon" shape that is
                            # the most expensive pattern in this project's
                            # history (v35 -854, cmtm9j09 -273).
# Projected combined: t/r bluffs 0 -> 36, value:bluff 6.56:1 -> 4.92:1.
# Deliberately short of the 2-3:1 reference - move once, measure, then widen.
SIZING_SCHEME_ENABLED = True    # ON from v46 (2026-09-04 21:05). Enabled after
                                # two clean polarization runs (v44+v45, 143
                                # hands): VPIP 0.590 -> 0.371/0.435, call:raise
                                # 1.88:1 -> 0.50/0.54, arena AF 0.540 -> 0.688,
                                # BB exemption 0 folds across 79 firings.

# AGGRESSION FOLD (§18). Refuse the pot-odds call when the opponent has shown
# real aggression this hand, whatever the price says.
#
# WHY THE PRICE TEST IS NOT ENOUGH: `equity >= pot_odds + call_margin` consumes
# estimate_equity's number at face value. Calibration by aggression band (§12)
# measured that number as +0.081 optimistic in aggregate but -0.181 at
# aggression 4-6 - i.e. in exactly this band the estimate is inflated, and the
# pot-odds test has no way to know how much is riding on it.
#
# PRE-REGISTERED, NOT FITTED. The bar was written into DESIGN_BACKLOG §18
# BEFORE the data arrived: build at >5:1 losing:winning hands, park at ~3:1. It
# broke 16:3 = 5.33:1 on 2026-09-10. The threshold is 4.0 rather than 5+ because
# the agg>=4 band already carried ~65% of the pot_odds_call loss; tightening it
# would shrink the population without any evidence justifying the narrower cut.
#
# NO MADE-HAND EXEMPTION ON PURPOSE. The bucket's winners are 3 hands at +190.
# Carving them out would be fitting to noise. If production shows the rule
# folding too much, add the exemption THEN - with its own bar.
AGGRESSION_FOLD_ENABLED = True
AGGRESSION_FOLD_THRESHOLD = 4.0


def board_texture(board_raw):
    """-> ('dry'|'wet', size multiplier) from the VISIBLE board only.

    Deterministic - max rank-count, suit-count and connectedness read straight
    off the community cards. Consumes no model output, so it carries no
    circularity risk (PRINCIPLES #9), and it is knowable at the decision point
    rather than only at showdown (PRINCIPLES #10).
    """
    if not board_raw or len(board_raw) < 3:
        return "n/a", 1.0
    try:
        ranks = sorted({"23456789TJQKA".index(c[0]) for c in board_raw})
        suits = {}
        for c in board_raw:
            suits[c[1]] = suits.get(c[1], 0) + 1
        paired = len(set(c[0] for c in board_raw)) < len(board_raw)
        three_flush = max(suits.values()) >= 3
        connected = (len(ranks) >= 3
                     and any(ranks[i + 2] - ranks[i] <= 4
                             for i in range(len(ranks) - 2)))
    except Exception:
        return "n/a", 1.0
    if three_flush or paired or connected:
        return "wet", SIZE_TEXTURE_WET
    return "dry", SIZE_TEXTURE_DRY


def scheme_size(street, board_raw):
    """Bet size as a fraction of pot, from street and texture ONLY.

    Takes no equity and no made_category argument by design - the tell this
    replaces existed because size was a function of hand strength.
    """
    base = SIZE_BY_STREET.get(street)
    if base is None:
        return None
    _t, mult = board_texture(board_raw)
    return base * mult


def _rfi_position(position):
    """Map _compute_position output to a seat label. frac 0 = SB, 1 = BTN."""
    frac, n = position.get("frac"), position.get("n") or 0
    if frac is None or n < 2:
        return "HJ"                       # neutral default
    idx = round(frac * (n - 1))
    if idx == 0:                          return "SB"
    if idx == 1:                          return "BB"
    if idx == n - 1:                      return "BTN"
    if idx == n - 2:                      return "CO"
    if idx == n - 3:                      return "HJ"
    return "EARLY"


def rfi_should_open(hole_raw, position):
    """True if this hand should be RAISED from this seat."""
    if not STAGE1_RFI_ENABLED:
        return False, None
    tier = _rfi_tier(hole_raw)
    if tier is None:
        return False, None
    pos = _rfi_position(position)
    return tier <= _RFI_MAX_TIER.get(pos, 2), pos



def alpha_breakeven(pot, bet):
    """Fold frequency a PURE bluff needs to break even: bet / (pot + bet).
    Half-pot 0.33, pot 0.50, 2x pot 0.67."""
    tot = (pot or 0) + (bet or 0)
    return (bet / tot) if tot > 0 else 1.0


def fold_equity_in_domain(equity, street, n_opponents):
    """Is this spot inside the domain where P_FOLD_SINGLE was measured?"""
    if street in FE_EXCLUDE_STREETS:
        return False
    if (n_opponents or 1) > FE_MAX_OPPONENTS:
        return False
    e = equity or 0
    return FE_MIN_EQUITY <= e <= FE_MAX_EQUITY


def fold_equity_decision(equity, pot, bet, n_opponents, street=None, tightness=None,
                          streets_left=0):
    """Return (should_bet, detail). Compares EV of betting vs EV of checking.

        EV_bet   = P(fold)*pot + (1-P(fold)) * (eq_called*(pot + 2*bet) - bet)
        EV_check = equity*pot + equity*CHECK_FUTURE_VALUE*pot*streets_left

    Multiway collapse is NOT a special case - it falls out of raising the
    single-opponent fold probability to the power of the opponent count."""
    detail = {"in_domain": False}
    if not fold_equity_in_domain(equity, street, n_opponents):
        return False, detail
    n = max(1, n_opponents or 1)
    p_single = P_FOLD_SINGLE
    # The API does NOT expose fold-to-cbet per opponent (agent-stats returns
    # vpip/pfr/threeBetPct/af/bluffPct/wtsd/wsd only), so the brief's
    # >=60%/<=35% fold-to-cbet gate cannot be implemented. tightness is the
    # only available proxy.
    if tightness == "tight":
        p_single = min(0.75, p_single + 0.10)
    elif tightness == "loose":
        p_single = max(0.30, p_single - 0.10)
    p_fold = p_single ** n
    eq = equity or 0
    eq_called = eq * CALLED_EQUITY_HAIRCUT
    ev_bet = p_fold * pot + (1 - p_fold) * (eq_called * (pot + 2 * bet) - bet)
    ev_check = eq * pot + eq * CHECK_FUTURE_VALUE * pot * max(0, streets_left)
    detail = {"in_domain": True, "p_fold": round(p_fold, 3),
              "alpha": round(alpha_breakeven(pot, bet), 3),
              "ev_bet": round(ev_bet, 1), "ev_check": round(ev_check, 1),
              "streets_left": streets_left, "n_opp": n}
    edge = (ev_bet - ev_check) / pot if pot else 0.0
    detail["edge"] = round(edge, 4)
    return (ev_bet > ev_check + MIN_EDGE * pot), detail


_STREETS_LEFT = {"Flop": 2, "Turn": 1, "River": 0}


# --- Value-bet sizing trial (2026-09-02) --------------------------------------
# Was a flat 0.75 here (landing ~0.72 pot after looseness modulation). Measured
# across 969 attributed decisions: weak hands (cat 0-1) and strong hands (cat
# 2+) were bet at an IDENTICAL median bet_to_pot of 0.71 vs 0.72 - the sizing
# rule keys only on equity and has two output values, so it cannot polarize.
# See DESIGN_BACKLOG.md "Confirmed structural gaps" (B).
#
# TRIAL, reversible in one line: raise ONLY the high-equity value arm to 0.90.
# The equity<=0.82 arm deliberately stays at 0.5 - widening the gap between the
# two is what makes this polarizing rather than just "bet bigger everywhere".
# Scope is narrow on purpose: fires on ~3.8% of decisions (37 of 969), touches
# no hand selection, and cannot trip the stack-risk cap (that needs equity <
# 0.80; this arm is all >0.82). One value bet in 37 is projected to newly trip
# the velocity brake, which downgrades to a check - safe, and logged.
VALUE_SIZE_HIGH = 0.90

STACK_VELOCITY_MADE_HAND_EXEMPT = 4

# --- Pot-odds exemption -------------------------------------------------
# The brake exists to distrust our own equity ESTIMATE when commitment spikes.
# That distrust is only worth acting on when the price is high enough for the
# estimate to matter. Below some price the call is correct at an equity lower
# than anything the model could plausibly be overstating, and the brake has
# nothing left to protect.
#
# The bar is deliberately LOW, and deliberately NOT "a big gap between equity
# and pot odds". A gap or ratio test does not survive contact with the real
# hands - the four reference decisions are:
#
#   hand        street action equity pot_odds opp_raises   correct verdict
#   cmtktqkt    Turn   call    0.955    0.049      1       exempt (trips, 19.5:1,
#                                                          brake forfeited ~6000)
#   cmtkwn8t    Turn   raise   0.861    0.282      0       ? (-69)
#   cmtcarsk    Turn   raise   0.828    0.294      0       BRAKE (the bust this
#   cmtcarsk    Turn   call    0.828    0.209      1       BRAKE  brake was built for)
#   cmtd2ab0    Flop   call    0.954    0.397      3       BRAKE (-1000)
#
# cmtkwn8t (0.282) and cmtcarsk's turn raise (0.294) are 0.012 apart, have the
# same opp_raises, and both show ~0.85 equity. NO threshold on pot odds, on the
# equity-minus-pot-odds gap, or on the equity/pot-odds ratio separates them.
# Any bar loose enough to exempt cmtkwn8t also un-brakes cmtcarsk's fatal call
# at 0.209 - reintroducing the exact bust the brake exists to prevent. So the
# bar is set where the "regardless of model error" argument is actually true,
# which exempts cmtktqkt alone. See DESIGN_BACKLOG.md.
STACK_VELOCITY_POT_ODDS_EXEMPT = 0.12
# Belt-and-braces: the price being cheap is not on its own a reason to commit
# if we also think we are behind. Cheap insurance, and it makes the intent
# legible at the call site.
STACK_VELOCITY_POT_ODDS_MIN_RATIO = 3.0
# Scoped to made hands. The justification for a 0.12 floor is that ANY made
# hand holds at least ~12% against any range, so being wrong about WHICH made
# hand we have cannot make the call -EV. That argument does not transfer to
# draws or air, so neither does the exemption (Principle 9: distrust should be
# scoped to what is actually uncertain - and so should the removal of it).
STACK_VELOCITY_POT_ODDS_MIN_CATEGORY = 1


# --- Decision attribution ---------------------------------------------------
# Which branch produced the action. Derived from the (already unique) message
# rather than tagging all ~40 assignment sites, which would be churn with a
# real chance of missing one.
#
# Exists because v21's log could not answer "did the stalled guard cause this
# fold, or would pot odds have folded it anyway?" - that had to be
# reconstructed by hand from equity vs pot_odds arithmetic. A log that needs
# arithmetic to interpret is a log that will be misread.
#
# NOTE: the made-hand exemption never appears here, because it does not decide
# an action - it SKIPS the velocity brake, leaving whatever the normal logic
# chose. Look for shadow_brake.skipped == "made_hand" for that.
_DECIDED_BY = {
    "opening for value from here": "value_open",
    # Preflop polarization (v44). Separate labels per rule so the attribution
    # log can tell which one is doing the work - A and B target different
    # branches and are expected to move different stats.
    "not a hand I'd open with - folding rather than calling a raise": "polarize_fold_vs_raise",
    "raise or fold here - not calling with a hand I wouldn't open": "polarize_fold_branch_c",
    # Two distinct gates once shared this string, so the attribution log could
    # not tell a 3-bet that BYPASSED raise_thresh (the tier gate) from one that
    # CLEARED it. That is the exact distinction the v35 post-mortem turned on -
    # all 8 firings were sub-threshold, invisible under a single label.
    "premium hand, three-betting the top of my range": "value_3bet_tier",
    "premium hand, re-raising for value": "value_3bet",
    "isolating for value": "value_isolate",
    "raising it up, taking this pot down now": "value_iso_raise",
    "betting for value here": "value_bet",
    "betting with fold equity - checking is worth less here": "fold_equity_bet",
    "semi-bluffing with a draw, heads up": "semi_bluff",
    "raising for value here": "value_raise",
    "raising, well ahead here": "value_raise",
    "price is right for this equity": "pot_odds_call",
    "cheap price, defending": "pot_odds_call",
    "price is right, calling": "pot_odds_call",
    "opponent has shown real aggression - not paying this price": "aggression_fold",
    "not enough equity to pay this price": "pot_odds_fold",
    "not worth even the cheap price": "pot_odds_fold",
    "not enough equity to continue": "pot_odds_fold",
    "hand never developed - not paying another street with nothing": "stalled_guard",
    "capping risk - re-raising here outruns my edge, taking the cheaper line": "stack_risk_cap",
    "capping risk - not enough edge to justify this much of my stack": "stack_risk_cap",
    "capping risk - checking instead of over-committing": "stack_risk_cap",
    "capping risk - that moves too much of my stack at once, taking the cheaper line": "velocity_brake",
    "capping risk - taking the free card instead of committing this fast": "velocity_brake",
    "capping risk - too much of my stack committed in a single move": "velocity_brake",
    "checking my option": "check_free",
    "checking, taking a free card": "check_free",
    "taking the free option": "check_free",
    "taking the passive line": "passive_call",
    "no check available, calling": "forced_call",
    "no fold available, taking the call": "forced_call",
    "no fold available, calling": "forced_call",
    "folding, no cheap way to continue": "pot_odds_fold",
    "no good option here": "forced_fold",
    "only line available": "forced_only_legal",
    "playing it safe": "fallback_error_path",
}


def decided_by(msg):
    return _DECIDED_BY.get(msg, f"unmapped:{msg}")


# --- Stalled-hand guard -----------------------------------------------------
# Traced from 178 archived hands: 6 of the 7 medium losses (-158 of -200, 79%
# of all loss chips) share one shape. Call preflop with a marginal hand, whiff
# the flop, whiff the turn, and keep paying small individually-legal prices to
# see the next card. Example cmtgpv0o: equity 0.533 -> 0.372 -> 0.219 -> 0.182,
# calling twice on the way down, three opponents turning over flushes while we
# held king-high for -27.
#
# NOTHING else catches this. The velocity brake needs a large single jump and
# these commit 3-16 chips at a time. The made-hand exemption only ever permits
# braking, never causes it. range_strength works correctly and simply does not
# gate calling. And every one of these calls passes equity >= pot_odds +
# call_margin on its own arithmetic - cmtgt8rn's turn call was 0.253 equity
# against ~0.246 pot odds, legal by a hair.
#
# The trigger is made_category, NOT declining equity. That matters: equity is
# already discounted by range_strength as aggression rises, so a guard built on
# "equity is falling" would partly be reacting to its own upstream adjustment.
# made_category comes from _best_hand_rank(hole + board) - pure card reading,
# no model input anywhere. Same deterministic-fact reasoning that justifies the
# made-hand EXEMPTION, applied in the opposite direction (PRINCIPLES.md #9).
#
# Boundary is category 0 (literally nothing made), measured not guessed. On the
# 49 outcome-joined hands:
#     max_cat 0 -> fires 5, all losses, saves 140, costs 0 winners
#     max_cat 1 -> fires 6, catches NO extra losses, costs a +42 winner
# One category wider destroys value for nothing.
STALLED_MIN_STREET = "Turn"     # river-only fires zero times; money is already in
STALLED_MAX_CATEGORY = 0        # high card only
# Release valve. Deliberately a MODELED number, admitted as such, and only in
# the permissive direction - it lets calls through, so an error here fails safe
# rather than compounding the leak this guard exists to fix.
#
# KNOWN LIMIT, NOT PROVEN: this guard cannot tell "high card with no future"
# from "high card with nine outs" - a flush or straight draw is category 0
# until it hits. The archive contains a live instance: cmtgtore held high card
# through flop and turn and made a straight on the river. It escaped only
# because it happened to face no turn bet, not by design.
# 0.45 / 0.50 / 0.55 all performed IDENTICALLY on n=49 (5 firings, 5 losses,
# 0 winners touched), so the sample cannot distinguish them. Revisit if
# archived hands start showing real draws being folded here.
STALLED_EQUITY_RELEASE = 0.45
_STREET_INDEX = {"Preflop": 0, "Flop": 1, "Turn": 2, "River": 3}


def stalled_hand_fold(street, made_category, equity, to_call):
    """True if this CALL should be refused because the hand never developed.

    Only ever evaluated on a call facing a bet. Checking is free and is never
    touched - none of the traced leak hands lost a chip by checking."""
    if not to_call or to_call <= 0:
        return False
    if _STREET_INDEX.get(street, 0) < _STREET_INDEX[STALLED_MIN_STREET]:
        return False
    if made_category is None or made_category > STALLED_MAX_CATEGORY:
        return False
    if equity is not None and equity >= STALLED_EQUITY_RELEASE:
        return False
    return True


def evaluate_velocity_brake(street, delta_frac, equity, aggression_score, action,
                             to_call, total_available, bb_chips, can,
                             made_category=None, pot=None):
    """Return (verdict, detail) for the proposed velocity brake, or (None, detail).

    Pure function of already-computed values so it can be evaluated in shadow
    mode and unit-tested without a live table. `can` is a predicate for whether
    an action name is currently legal."""
    detail = {"delta_frac": round(delta_frac, 3), "street": street}
    if action not in ("call", "bet", "raise", "all-in"):
        return None, detail
    # Deterministic hand strength overrides the brake entirely. Checked before
    # the cap so no amount of aggression or commitment velocity can fold a
    # flush-or-better.
    if made_category is not None and made_category >= STACK_VELOCITY_MADE_HAND_EXEMPT:
        detail["skipped"] = "made_hand"
        detail["made_category"] = made_category
        return None, detail
    # Pot-odds exemption. Same shape as the made-hand exemption above: a SKIP
    # before the cap, not a looser cap. A looser cap still folds these hands
    # once the commitment jump is large enough, which is precisely the case
    # where the price is best.
    if pot is not None and to_call > 0:
        _po = to_call / (pot + to_call) if (pot + to_call) > 0 else 1.0
        detail["pot_odds"] = round(_po, 3)
        if (_po <= STACK_VELOCITY_POT_ODDS_EXEMPT
                and equity >= _po * STACK_VELOCITY_POT_ODDS_MIN_RATIO
                and made_category is not None
                and made_category >= STACK_VELOCITY_POT_ODDS_MIN_CATEGORY):
            detail["skipped"] = "pot_odds"
            return None, detail
    if bb_chips and total_available < STACK_VELOCITY_MIN_BB * bb_chips:
        detail["skipped"] = "short_stack"
        return None, detail
    cap = velocity_cap_for(street, aggression_score)
    detail["cap"] = round(cap, 3)
    detail["agg"] = round(aggression_score or 0.0, 2)
    if delta_frac <= cap:
        return None, detail
    # Prefer the cheapest legal continuation over folding. Checking is free, so
    # never fold when nobody has bet into us - the original draft of this brake
    # folded on a `bet`, throwing away all equity for nothing.
    #
    # The call_delta guard verifies the DOWNGRADE TARGET is itself under the
    # cap. Without it "downgrade to call" can hand back a call that is just as
    # oversized as the raise it replaced, which defeats the brake silently.
    # This guard existed in the original inline draft and was dropped when the
    # logic was refactored into this function - restored here.
    call_delta = (to_call / total_available) if total_available else 1.0
    if action in ("raise", "bet", "all-in") and to_call > 0 and can("call") \
            and call_delta <= cap:
        detail["call_delta"] = round(call_delta, 3)
        return "call", detail
    if to_call <= 0 and can("check"):
        return "check", detail
    # A REFUSED RAISE falls through to call, never to fold.
    #
    # The brake governs ESCALATION. When it refuses a raise, calling is a
    # strictly smaller commitment than the action just refused, so folding is a
    # LARGER deviation from the proposed action than calling is - the brake
    # would be overshooting its own remit. The guard above only establishes that
    # the call is not itself under the cap; that is a reason not to call it a
    # clean downgrade, not a reason to fold.
    #
    # Note this needs NO trust in the equity estimate, which is exactly what
    # separates it from the pot-odds exemption we rejected. It never asks
    # whether the call is +EV, only that it is cheaper than the refused raise.
    #
    # Scoped to raise/bet/all-in deliberately. When the PROPOSED action is
    # itself a call, there is no larger refused action for a call to be smaller
    # than, and folding stays correct - that is the cmtcarsk (action="call",
    # to_call 505) and cmtd2ab0 (action="call", to_call 793) shape, both of
    # which must keep folding. Both busts are untouched by this change.
    #
    # Found via cmtlxu0b (v37): two pair, 86.6% equity, 3.7:1, +116 EV call,
    # folded because the downgrade target failed the cap. Same fallthrough
    # shape as the STACK_RISK_CAP bug, different trigger - the intended action
    # was legal but unaffordable rather than illegal, which is why an audit
    # looking only for "illegal" missed it.
    if action in ("raise", "bet", "all-in") and to_call > 0 and can("call"):
        detail["fallthrough"] = "call_over_cap"
        detail["call_delta"] = round(call_delta, 3)
        return "call", detail
    if can("fold"):
        return "fold", detail
    return None, detail

# call_margin is the equity we demand ON TOP of raw pot odds before calling.
# It must never go below zero: equity < pot_odds is a losing call on immediate
# odds, and this strategy models no implied odds and no fold equity, so it has
# nothing to justify one with. The margin is built from several signed bumps
# (loose_bonus, pos_bonus, escalation_bump, opp_bump) and opponent_read_bump
# alone reaches -0.15, which was enough to push the total negative and call
# BELOW pot odds. Observed live in Playground S16, twice in one run:
#   Turn,    to_call 92, equity 0.215 vs pot_odds 0.261, opp_bump -0.15
#            -> call_margin -0.0785, required equity 0.1825, called.
#   Preflop, to_call  5, equity 0.198 vs pot_odds 0.278, opp_bump -0.15
# The negative bump is still useful - it should make us look up a bluffy
# opponent more often by shrinking the margin toward zero - but zero is the
# floor. Both call sites clamp with max(0.0, ...) after all bumps are summed.
# (The preflop site's inner max() predated this and clamped only the
# loose/position part, leaving opp_bump free to drive the total negative.)


def decide_live(table):
    """v7: mirrors strategy.py's v6 decide() (v4's equity-vs-pot-odds shape -
    wide calling, selective raising - as the primary preflop driver, with
    position awareness and opponent-tendency-aware sizing layered on top; see
    strategy.py's module docstring for why v5's Chen-heavy preflop blend was
    dropped) plus this file's own live-only additions: certain/pending
    opponent-count correction, street-aware pending weight, and the
    escalating-re-raise + stack-risk-cap safeguards documented in the module
    docstring above."""
    allowed_names, to_call, call_amount = set(), 0, 0
    try:
        allowed = table.get("allowedActions") or {}
        allowed_names = S._capability_names(allowed)
        self_seat = S._find_self_seat(table)

        hole_raw = self_seat.get("holeCards") or []
        board_raw = table.get("boardCards") or []
        pot = table.get("potChips", 0) or 0
        current_bet = table.get("currentBet")
        my_bet = self_seat.get("currentBetChips", 0) or 0
        stack = self_seat.get("stackChips")
        # Fraction of our total chips already in this hand, BEFORE we act.
        # Distinct from the debug field `stack_commit_frac`, which includes the
        # amount we are about to commit. The semi-bluff ceiling needs the
        # pre-decision figure: the question is "how deep am I already in", not
        # "how deep would this bet put me".
        _committed = self_seat.get("totalCommittedChips", my_bet) or my_bet
        _committed_frac = (_committed / (_committed + stack)
                           if stack is not None and (_committed + stack) > 0 else 0.0)

        call_to_amount = allowed.get("callToAmount")
        call_chips = allowed.get("callChips")
        if call_chips is None:
            call_chips = allowed.get("callAmount")
        if call_chips is None and call_to_amount is not None:
            call_chips = max(0, int(call_to_amount) - int(my_bet))
        if call_chips is None and current_bet is not None:
            call_chips = max(0, int(current_bet) - int(my_bet))
        to_call = call_chips or 0
        call_amount = call_to_amount if call_to_amount is not None else (current_bet if current_bet is not None else my_bet + to_call)

        min_bet_to = allowed.get("minBet")
        min_raise_to = allowed.get("minRaiseTo", table.get("minRaiseTo"))
        max_commit = allowed.get("maxCommit")
        allin_to = allowed.get("allInToAmount")
        bet_range = allowed.get("betRange") or []
        raise_range = allowed.get("raiseRange") or []

        seats = table.get("seats") or []
        active_seats = [s for s in seats if isinstance(s, dict) and s.get("status") in ("Active", "AllIn") and s is not self_seat]
        opponent_ids = [s.get("agentId") for s in active_seats if s.get("agentId")]

        # Split into opponents who have already voluntarily acted this street
        # (certain contestants) vs. seats still to act (pending - most fold
        # before showdown, so they shouldn't count as full-strength random
        # hands we must beat simultaneously). See strategy.py's estimate_equity
        # docstring; fixes v6 folding hands like AJs/AJo to a single raise
        # just because 4 other unacted seats are still technically "Active".
        acted_nums = S._acted_seat_numbers(table)
        certain_seats = [s for s in active_seats if s.get("seatNumber") in acted_nums]
        pending_seats = [s for s in active_seats if s.get("seatNumber") not in acted_nums]
        num_opponents = len(certain_seats)
        num_pending = len(pending_seats)
        if num_opponents == 0 and num_pending == 0:
            num_opponents = 1

        S._update_opponent_model(table)
        looseness, aggression = S._opp_profile(opponent_ids)
        position = S._compute_position(table, self_seat)
        pos_bonus = (position["frac"] - 0.5) * 0.20
        loose_bonus = (looseness - 0.4) * 0.10

        # Safeguard 1/2: an opponent raise is real information no equity-vs-
        # random-hand simulation captures (no range modeling exists here -
        # see module docstring). The first raise we face is already priced by
        # the normal raise/isolate thresholds; every ADDITIONAL raise on the
        # same street (i.e. they re-raised us back) bumps the bar further, so
        # a re-raise war requires escalating equity to keep matching instead
        # of being fought purely on a static threshold. This is what busted
        # the whole stack in round 51 (three straight turn re-raises, all
        # "justified" by a flat 72-77% equity read with zero read on the fact
        # that a re-raise almost always means we're beat).
        opp_raises = S._opponent_raises_this_street(table, self_seat.get("seatNumber"))
        escalation_bump = 0.10 * max(0, opp_raises - 1)

        # Real per-opponent read (see get_opponent_stats above): who most
        # recently bet/raised on this street, and what do their actual
        # long-run stats say about how much a bet from them means. Applied
        # alongside escalation_bump at the same threshold sites - this one
        # fires even on the FIRST bet/raise faced (escalation_bump doesn't),
        # which is exactly the round-24 shape (called a single big bet from
        # one specific opponent with no read on them at all and lost).
        aggressor_seat = S._last_aggressor_seat(table, self_seat.get("seatNumber"))
        opp_bump = 0.0
        if aggressor_seat is not None:
            aggressor_id = next((s.get("agentId") for s in seats
                                  if isinstance(s, dict) and s.get("seatNumber") == aggressor_seat), None)
            if aggressor_id:
                try:
                    opp_bump = S.opponent_read_bump(get_opponent_stats(aggressor_id))
                except Exception:
                    opp_bump = 0.0

        try:
            hole = [S._parse_card(c) for c in hole_raw if c]
            board = [S._parse_card(c) for c in board_raw if c]
        except Exception:
            hole, board = [], []

        street = table.get("street")
        pending_weight = _PENDING_WEIGHT_BY_STREET.get(street, 0.35)

        # Trial counts were 250/150, which put the estimator's own sampling
        # noise on the same scale as the decision margins it feeds. Measured
        # on a real spot (Jd3c on 7d Jc Qc Kd vs 3 opponents), 40 repeats of
        # the SAME spot: trials=250 -> stdev 0.029, spread 0.12; trials=2000
        # -> stdev 0.010, spread 0.042. With call_margin at 0.06 and the
        # threshold gaps around 0.04-0.10, a stdev of 0.029 means a
        # meaningful share of borderline calls/folds were decided by RNG
        # rather than by the hand. Cost at 2000 trials is ~0.55s postflop /
        # ~0.47s preflop against a 30-second action deadline, so the extra
        # precision is essentially free. Kept well under the deadline rather
        # than pushed to 5000 (~1.4s) to leave headroom for a slow request.
        # Leak 6 (FINDINGS.md): estimate_equity's opponents were uniformly
        # random cards regardless of how the hand has gone, overstating our
        # equity most exactly when an opponent has proven otherwise. Once
        # someone has voluntarily continued (called or raised) on a prior
        # street this hand, bias the sim away from complete air for them -
        # 0 on the first bet of a hand (no history yet), rising by street.
        # Capped well under 1.0: this is a coarse "at least a pair" bar, not
        # a real range model, so it shouldn't be trusted to fully correct for
        # a genuinely premium continuing range.
        # Ungated from Turn/River-only to ALL postflop streets after hand
        # cmtd2ab0 busted a full stack in a three-raise war fought entirely on
        # the FLOP, where range_strength was 0.0 and contributed nothing.
        #
        # Ungating alone would have been a no-op: _opponent_continuation_count
        # only counts streets STRICTLY BEFORE the current one and excludes
        # Preflop, so on the flop it is structurally always 0 (verified). The
        # only aggression signal that exists on the flop is raises within the
        # street itself, so opp_raises is folded in here. Still purely a
        # discount on an overconfident equity number - it can only pull equity
        # down, never force a fold on its own.
        # Cumulative aggression across the WHOLE hand (see strategy.py). The
        # previous per-street version both ignored bets and reset at street
        # boundaries - on cmtewk95 the discount fell 0.70 -> 0.35 going into
        # the turn, right as the villain's second flop raise should have made
        # it stronger, and that hand lost 421 chips.
        # Our own made hand, computed exactly from the cards on the table.
        # Logged on every postflop decision so questions like "was this bet
        # sized up on a made hand or on a drawing estimate?" are answerable
        # from the log alone. They currently are not: /texas/recent-tables is
        # a rolling 50-hand window and is the only source of hole cards, so
        # any hand older than that has no recoverable ground truth.
        made_category = None
        if len(hole) == 2 and len(board) >= 3:
            try:
                made_category = S._best_hand_rank(hole + board)[0]
            except Exception:
                made_category = None

        range_strength = 0.0
        aggression_score = 0.0
        if street in ("Flop", "Turn", "River"):
            aggression_score = S.cumulative_aggression(table, self_seat.get("seatNumber"))
            range_strength = S.aggression_to_range_strength(aggression_score)

        try:
            equity = S.estimate_equity(hole, board, num_opponents=num_opponents, pending_opponents=num_pending,
                                        pending_weight=pending_weight, range_strength=range_strength,
                                        trials=2000) if len(hole) == 2 else 0.5
        except Exception:
            equity = 0.5

        chen_norm = min(1.0, S._chen_score(hole) / 20.0) if len(hole) == 2 else 0.5

        def has(name):
            return not allowed_names or name in allowed_names

        # NOTE: the `looseness` multiplier below is INTENTIONAL, not a leftover.
        # It perturbs the requested fraction by up to +-15% based on the
        # OPPONENT profile (S._opp_profile), sizing up against loose fields and
        # down against tight ones. That is sound poker and it does NOT reintroduce
        # the size tell the scheme exists to remove: the tell was size varying
        # with OUR hand strength, which is readable. Varying with the opponent's
        # tendencies is not readable by them and is the point of having a read.
        # Do not remove this thinking it undermines the sizing scheme.
        def sized_bet(fraction):
            frac = max(0.3, min(1.1, fraction * (1.0 + 0.25 * (looseness - 0.4))))
            lo = min_bet_to if min_bet_to is not None else (bet_range[0] if len(bet_range) > 0 else None)
            hi = max_commit if max_commit is not None else (bet_range[1] if len(bet_range) > 1 else stack)
            target = int(my_bet + max(pot, 10) * frac)
            return S._clamp_to_amount(target, lo, hi)

        def sized_raise(fraction):
            frac = max(0.3, min(1.1, fraction * (1.0 + 0.25 * (looseness - 0.4))))
            lo = min_raise_to if min_raise_to is not None else (raise_range[0] if len(raise_range) > 0 else None)
            hi = max_commit if max_commit is not None else (raise_range[1] if len(raise_range) > 1 else stack)
            base = call_amount if call_amount else (my_bet + to_call)
            target = int(base + max(pot, 10) * frac)
            return S._clamp_to_amount(target, lo, hi)

        def bb_raise(bb_multiple):
            """Absolute raise-to in big blinds. Preflop opens are sized off the
            blind, not off a pot that has barely moved - see ISO_RAISE_BASE_BB.
            Falls back to the pot-fraction sizer if the table does not report a
            big blind, so a missing field degrades instead of raising."""
            if not bb_chips:
                return sized_raise(0.7)
            lo = min_raise_to if min_raise_to is not None else (
                raise_range[0] if len(raise_range) > 0 else None)
            hi = max_commit if max_commit is not None else (
                raise_range[1] if len(raise_range) > 1 else stack)
            return S._clamp_to_amount(int(bb_chips * bb_multiple), lo, hi)

        _fe_detail = {}
        _fe_amount = 0

        def _fe_bet(pot_, my_bet_, min_bet_to_, max_commit_, bet_range_, stack_):
            """Would a 0.5-pot semi-bluff beat checking, once P(fold) is priced?"""
            nonlocal _fe_detail, _fe_amount
            try:
                amt = sized_bet(0.5)
                new_chips = max(0, int(amt) - int(my_bet_ or 0))
                if new_chips <= 0:
                    return False
                tightness = None
                if aggressor_seat is not None:
                    pass  # nobody has bet into us here by definition (to_call<=0)
                ok, det = fold_equity_decision(
                    equity, pot_, new_chips, num_opponents, street=street,
                    tightness=tightness, streets_left=_STREETS_LEFT.get(street, 0))
                _fe_detail, _fe_amount = det, amt
                return ok
            except Exception:
                return False

        def below_min(amount, lo):
            try:
                return lo is not None and int(amount) < int(lo)
            except (TypeError, ValueError):
                return False

        pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0.0
        bb_chips = table.get("bigBlindChips")

        if street == "Preflop":
            strength = equity + 0.08 * (chen_norm - 0.5)
            raise_pos_adj = pos_bonus * 0.5 + loose_bonus * 0.4
            # Clamped at >= 0 AFTER every bump: see _MIN_CALL_MARGIN note below.
            call_margin = max(0.0, max(0.0, 0.06 - loose_bonus - pos_bonus * 0.3) + escalation_bump + opp_bump)
            facing_raise = bb_chips is not None and (current_bet or 0) > bb_chips

            rfi_open, rfi_pos = rfi_should_open(hole_raw, position)
            # Seat label derived independently of rfi_pos: rfi_should_open
            # returns (False, None) for untiered hands, so rfi_pos is None for
            # precisely the hands the polarization rules act on. Testing the BB
            # exemption against it made the exemption unreachable - caught by
            # test_preflop_polarize before this ever ran live.
            _seat_label = _rfi_position(position)

            if to_call <= 0:
                open_thresh = 0.68 - raise_pos_adj
                # Stage 1 (Option C): the RFI table raises hands the equity gate
                # would have limped or checked. Purely ADDITIVE - it never folds
                # anything, so VPIP is untouched and only PFR moves.
                if rfi_open and has("raise"):
                    amount = sized_raise(0.65 + 0.15 * max(0, looseness - 0.4))
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "opening for value from here"
                elif strength >= open_thresh and has("raise"):
                    amount = sized_raise(0.65 + 0.15 * max(0, looseness - 0.4))
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "opening for value from here"
                elif has("check"):
                    action, amount, msg = "check", 0, "checking my option"
                elif has("call"):
                    action, amount, msg = "call", call_amount, "taking the passive line"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "no good option here"
                else:
                    action, amount, msg = next(iter(allowed_names), "check"), 0, "only line available"
            elif facing_raise:
                raise_thresh = 0.72 - raise_pos_adj + escalation_bump + opp_bump
                # 3-bet the top of the RFI range instead of flat-calling it.
                # Restricted to tiers 1-2 (~QQ+/AK/AQ class) to keep 3-bet% in
                # the 5-6% band rather than 3-betting every tier-3 open.
                #
                # opp_raises <= 1 caps this at the FIRST re-raise. Without it the
                # gate is purely a card-class test and re-fires every time the
                # pot is raised back, producing exactly the escalation wars that
                # cost v35 its stack: all 8 value_3bet firings that run were
                # below their own raise_thresh, and the 3 that re-fired into an
                # already-escalated pot (cmtloxo0 104, cmtlp5gg 77, cmtluq1d 83)
                # account for -623 of the -854. The tier gate stays the entry
                # condition deliberately - adding `strength >= raise_thresh`
                # here would also cut OPENING 3-bet frequency and confound the
                # Stage 1 PFR measurement. This cap only stops re-fires.
                _t = _rfi_tier(hole_raw)
                if (STAGE1_RFI_ENABLED and _t is not None and _t <= 2
                        and opp_raises <= 1 and has("raise")):
                    amount = sized_raise(0.85)
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "premium hand, three-betting the top of my range"
                elif strength >= raise_thresh and has("raise"):
                    amount = sized_raise(0.85)
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "premium hand, re-raising for value"
                # RULE A: facing a raise with a hand below tier 3, fold rather
                # than call. If it is not good enough to OPEN it is not good
                # enough to call a raise. Checked before the price test on
                # purpose - the point is that a good price does not rescue an
                # unopenable hand. BB exempt: see PREFLOP_POLARIZE_ENABLED.
                elif (PREFLOP_POLARIZE_ENABLED and _t is None
                      and _seat_label != "BB" and has("fold")):
                    action, amount, msg = "fold", 0, \
                        "not a hand I'd open with - folding rather than calling a raise"
                elif equity >= pot_odds + call_margin and has("call"):
                    action, amount, msg = "call", call_amount, "price is right for this equity"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "not enough equity to pay this price"
                elif has("check"):
                    action, amount, msg = "check", 0, "taking the free option"
                elif has("call"):
                    action, amount, msg = "call", call_amount, "no fold available, taking the call"
                else:
                    action, amount, msg = next(iter(allowed_names), "fold"), call_amount, "only line available"
            else:
                isolate_thresh = 0.66 - raise_pos_adj + escalation_bump + opp_bump
                # Stage 1, branch C. THIS is the real raise-first-in spot.
                # Preflop we are always facing at least the big blind, so
                # `to_call <= 0` (branch A, where v33 wired the RFI table) is
                # unreachable: it fired on 0 of 102 preflop decisions across
                # v33's 81 hands, and `value_open` never appeared once in the
                # log. Everything that is actually an "open" - folded to us, or
                # limpers in front - arrives here instead.
                #
                # Still purely ADDITIVE: this raises hands the equity gate below
                # would have limped or called. Nothing new is folded, so VPIP is
                # untouched and only PFR moves.
                _limpers = 0
                try:
                    _limpers = S._limpers_this_street(table, self_seat.get("seatNumber"))
                except Exception:
                    _limpers = 0
                if rfi_open and has("raise"):
                    amount = bb_raise(ISO_RAISE_BASE_BB
                                      + ISO_RAISE_PER_LIMPER_BB * _limpers)
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = ("raising it up, taking this pot down now" if _limpers
                           else "opening for value from here")
                elif strength >= isolate_thresh and has("raise"):
                    amount = sized_raise(0.7)
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "isolating for value"
                # RULE B: branch C is RAISE-OR-FOLD - no calling. The tiered
                # hands were already raised by the RFI/iso arms above; anything
                # reaching here is untiered, so it folds instead of limping
                # along. This is the branch where 408 of our preflop calls live.
                # `check` still comes first when it is free - folding a free
                # option is never right (see test_never_fold_free_check).
                elif (PREFLOP_POLARIZE_ENABLED and to_call > 0
                      and _seat_label != "BB" and has("fold")):
                    action, amount, msg = "fold", 0, \
                        "raise or fold here - not calling with a hand I wouldn't open"
                elif equity >= pot_odds + call_margin and has("call"):
                    action, amount, msg = "call", call_amount, "cheap price, defending"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "not worth even the cheap price"
                elif has("check"):
                    action, amount, msg = "check", 0, "taking the free option"
                else:
                    action, amount, msg = next(iter(allowed_names), "fold"), call_amount, "only line available"
        else:
            value_thresh = 0.68 - pos_bonus * 0.3
            raise_thresh = 0.72 - pos_bonus * 0.3 + escalation_bump + opp_bump
            # Size from street x texture, NOT from equity. `_scheme` is None
            # when the scheme is off, in which case the original equity-keyed
            # sizing is used unchanged - that is the revert path.
            _scheme = scheme_size(street, board_raw) if SIZING_SCHEME_ENABLED else None
            if to_call <= 0:
                if equity >= value_thresh and has("bet"):
                    amount = sized_bet(_scheme if _scheme is not None
                                       else (VALUE_SIZE_HIGH if equity > 0.82 else 0.5))
                    if below_min(amount, min_bet_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "bet"
                    msg = "betting for value here"
                elif equity >= value_thresh and has("raise"):
                    amount = sized_raise(_scheme if _scheme is not None
                                         else (VALUE_SIZE_HIGH if equity > 0.82 else 0.5))
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "raising for value here"
                elif (FOLD_EQUITY_ENABLED and has("bet") and made_category is not None
                      and pot and _fe_bet(pot, my_bet, min_bet_to, max_commit, bet_range, stack)):
                    # Semi-bluff / fold-equity bet: equity is BELOW value_thresh,
                    # so the old logic could only check here. Bet anyway when the
                    # EV of betting beats the EV of checking once P(fold) is
                    # priced in. Sized at 0.5 pot deliberately - small enough that
                    # alpha stays low (a half-pot bet needs only ~33% folds), and
                    # it shares a size with the low value arm so the two are not
                    # distinguishable to opponents.
                    amount = _fe_amount
                    if below_min(amount, min_bet_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "bet"
                    msg = "betting with fold equity - checking is worth less here"
                # TURN/RIVER SEMI-BLUFF. Fires only where we would otherwise
                # CHECK - it converts checks to bets, never calls to raises, so
                # it cannot escalate a pot we are already in. All five
                # conditions are load-bearing; see the constants for why.
                elif (SIZING_SCHEME_ENABLED and has("bet")
                      and street in ("Turn", "River")
                      and made_category is not None
                      and made_category <= 1
                      and equity >= BLUFF_MIN_EQUITY
                      and (num_opponents or 0) <= BLUFF_MAX_OPPONENTS
                      and (aggression_score or 0.0) < BLUFF_MAX_AGGRESSION
                      and _committed_frac <= BLUFF_MAX_COMMIT
                      and _scheme is not None):
                    amount = sized_bet(_scheme)
                    if below_min(amount, min_bet_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "bet"
                    msg = "semi-bluffing with a draw, heads up"
                elif has("check"):
                    action, amount, msg = "check", 0, "checking, taking a free card"
                elif has("call"):
                    action, amount, msg = "call", call_amount, "no check available, calling"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "folding, no cheap way to continue"
                else:
                    action, amount, msg = next(iter(allowed_names), "check"), 0, "only line available"
            else:
                # Clamped at >= 0 AFTER every bump: see _MIN_CALL_MARGIN note below.
                call_margin = max(0.0, 0.06 - loose_bonus * 0.5 + escalation_bump + opp_bump)
                # Stalled-hand guard (see stalled_hand_fold above). Checked
                # before the call branch, but AFTER raising - a hand strong
                # enough to raise is never "stalled". Never touches check.
                if has("fold") and stalled_hand_fold(street, made_category, equity, to_call):
                    action, amount, msg = "fold", 0, \
                        "hand never developed - not paying another street with nothing"
                elif equity >= raise_thresh and has("raise"):
                    # Scheme applies to raises facing a bet too. v46 shipped
                    # with only the to_call<=0 BET arms wired, so this path kept
                    # the flat 0.75 and produced 1.06-1.07 pot raises - above the
                    # scheme's 0.759 ceiling. Because those unscheme'd raises
                    # happened to be the strong hands, measured
                    # corr(size, category) came out +0.690, WORSE than the +0.210
                    # being fixed. The projection covered ALL postflop
                    # aggression; the implementation covered a subset. Check that
                    # the two populations match before trusting a projection.
                    amount = sized_raise(_scheme if _scheme is not None else 0.75)
                    if below_min(amount, min_raise_to) and has("all-in"):
                        action, amount = "all-in", int(allin_to if allin_to is not None else amount)
                    else:
                        action = "raise"
                    msg = "raising, well ahead here"
                # AGGRESSION FOLD (§18). Scoped to THIS arm on purpose:
                # postflop, facing a bet, on the pot_odds_call path only. It
                # sits AFTER the raise arm, so a hand strong enough to raise
                # still raises - this only ever converts a CALL into a FOLD.
                # It cannot reach the two preflop pot_odds_call sites, where
                # aggression_score is absent/unreliable, and it cannot fire at
                # to_call == 0 because this whole block is the to_call > 0 else.
                elif (AGGRESSION_FOLD_ENABLED
                      and (aggression_score or 0.0) >= AGGRESSION_FOLD_THRESHOLD
                      and has("fold")):
                    # SHADOW LOG. Observation only - `action` below is
                    # unchanged, the fold still happens. shadow_action is
                    # COMPUTED, not assumed: it re-evaluates the exact arm this
                    # rule jumped ahead of, so a spot where the price test would
                    # itself have folded is recorded as a fold rather than
                    # silently counted as a prevented call.
                    _shadow_would = ("call" if (equity >= pot_odds + call_margin
                                                and has("call"))
                                     else ("fold" if has("fold")
                                           else ("check" if has("check") else None)))
                    shadow_record_fold(table.get("id") or table.get("tableId"), {
                        "street": street,
                        "equity": equity,
                        "pot": pot,
                        "to_call": to_call,
                        "aggression_score": aggression_score,
                        "made_category": made_category,
                        "shadow_action": _shadow_would,
                        "pot_odds": pot_odds,
                        "call_margin": round(call_margin, 4),
                        # NOT `my_total_committed` - that is computed further
                        # down and does not exist yet here. Same expression,
                        # read from the seat directly.
                        "hero_committed_at_fold":
                            (self_seat.get("totalCommittedChips", my_bet) or my_bet),
                        "board_at_fold": list(board_raw),
                        "hole_cards": list(hole_raw),
                        "num_opponents": num_opponents,
                        # Needed to read `winners` against ourselves at
                        # settlement. Without it the settled half names
                        # winning seats with nothing to compare them to.
                        "hero_agent_id": my_agent_id(),
                        "hero_seat": self_seat.get("seatNumber"),
                    })
                    action, amount, msg = "fold", 0,                         "opponent has shown real aggression - not paying this price"
                elif equity >= pot_odds + call_margin and has("call"):
                    action, amount, msg = "call", call_amount, "price is right, calling"
                elif has("fold"):
                    action, amount, msg = "fold", 0, "not enough equity to continue"
                elif has("check"):
                    action, amount, msg = "check", 0, "taking the free option"
                elif has("call"):
                    action, amount, msg = "call", call_amount, "no fold available, calling"
                else:
                    action, amount, msg = next(iter(allowed_names), "fold"), call_amount, "only line available"

        # Safeguard 2: hard stack-risk cap, independent of everything above.
        # Downgrades a too-large commitment to the cheapest still-legal
        # continuation (call over raise/bet/all-in, fold over call) rather
        # than trusting a bare equity number with the whole stack behind it.
        #
        # Uses totalCommittedChips (whole-hand cumulative commitment), not
        # just my_bet (currentBetChips, which the schema documents as
        # street-scoped and resets to 0 every new street). The original
        # version based total_available/commit_frac on my_bet alone, so it
        # only ever saw THIS street's commitment against the stack we had at
        # the START of that street - a hand where we call material bets on
        # several streets in a row, each individually well under
        # STACK_RISK_CAP, could bleed most of the stack across the whole hand
        # with this safeguard never firing once.
        #
        # Scope of this fix, measured against the two biggest real losses on
        # record (v7_9 -193 chips, v7_24 -249 chips): it roughly DOUBLES the
        # fraction the cap actually sees on later streets (v7_24 flop
        # 0.163 -> 0.304; v7_9 river 0.106 -> 0.210), confirming the old
        # denominator materially under-counted. But neither hand crosses
        # STACK_RISK_CAP=0.5 even on the corrected basis, so this change
        # alone would NOT have prevented either loss - do not read it as a
        # fix for them. It makes the existing circuit breaker measure the
        # right quantity; catching multi-street call-downs of this shape
        # needs a separate, lower whole-hand threshold (see FINDINGS.md).
        my_total_committed = self_seat.get("totalCommittedChips", my_bet) or my_bet
        already_committed_other_streets = max(0, my_total_committed - my_bet)
        if stack is not None and action in ("call", "bet", "raise", "all-in") and amount:
            total_available = my_total_committed + stack
            if total_available > 0:
                commit_frac = (amount + already_committed_other_streets) / total_available
                if commit_frac > STACK_RISK_CAP and equity < STACK_RISK_EQUITY_FLOOR:
                    if action in ("raise", "bet", "all-in") and to_call > 0 and has("call") \
                            and (my_bet + to_call + already_committed_other_streets) / total_available <= STACK_RISK_CAP:
                        action, amount, msg = "call", call_amount, "capping risk - re-raising here outruns my edge, taking the cheaper line"
                    elif to_call == 0 and has("check"):
                        action, amount, msg = "check", 0, "capping risk - checking instead of over-committing"
                    elif has("fold"):
                        action, amount, msg = "fold", 0, "capping risk - not enough edge to justify this much of my stack"

        # Velocity brake, SHADOW MODE: evaluate and record what it would have
        # done, but never override the live action while
        # STACK_VELOCITY_SHADOW_MODE is True. Flip that flag only after the
        # logged false-positive rate on real hands justifies enforcement.
        shadow = {}
        try:
            if stack is not None:
                _total_avail = my_total_committed + stack
                if _total_avail > 0:
                    _delta = max(0.0, (amount - my_bet) / _total_avail) if amount else 0.0
                    _verdict, shadow = evaluate_velocity_brake(
                        street, _delta, equity, aggression_score, action, to_call,
                        _total_avail, bb_chips, has, made_category=made_category,
                        pot=pot)
                    shadow["would"] = _verdict
                    shadow["actual"] = action
                    if _verdict and not STACK_VELOCITY_SHADOW_MODE:
                        if _verdict == "call":
                            action, amount, msg = "call", call_amount, "capping risk - that moves too much of my stack at once, taking the cheaper line"
                        elif _verdict == "check":
                            action, amount, msg = "check", 0, "capping risk - taking the free card instead of committing this fast"
                        else:
                            action, amount, msg = "fold", 0, "capping risk - too much of my stack committed in a single move"
        except Exception:
            shadow = {"error": "velocity brake evaluation failed"}

        result = {"action": action, "amount": int(amount), "message": msg}
        if allowed.get("reasoningRequired"):
            result["reasoning"] = build_reasoning(equity, pot_odds, action)
        thresholds = {name: round(locals()[name], 3) for name in
                      ("open_thresh", "raise_thresh", "isolate_thresh", "value_thresh") if name in locals()}
        result["_debug"] = {
            "street": street, "equity": round(equity, 3), "pot_odds": round(pot_odds, 3),
            "num_opponents": num_opponents, "num_pending": num_pending, "pending_weight": pending_weight,
            "position_frac": round(position["frac"], 2), "looseness": round(looseness, 2),
            "aggression": round(aggression, 2), "thresholds": thresholds,
            "opp_raises_this_street": opp_raises, "escalation_bump": round(escalation_bump, 2),
            "aggressor_seat": aggressor_seat, "opp_bump": round(opp_bump, 3),
            "range_strength": round(range_strength, 2),
            "aggression_score": round(aggression_score, 2),
            "made_category": made_category,
            "rfi_open": rfi_open if street == "Preflop" else None,
            "limpers": _limpers if street == "Preflop" and "_limpers" in locals() else None,
            "rfi_pos": rfi_pos if street == "Preflop" else None,
            # Seat label derived independently of rfi_pos, which is None for
            # every UNTIERED hand - the exact population the polarization rules
            # act on. Logged so the BB exemption can be audited from the run log.
            "seat_label": (_seat_label if street == "Preflop"
                           and "_seat_label" in locals() else None),
            "stalled_guard": stalled_hand_fold(street, made_category, equity, to_call),
            "decided_by": decided_by(msg), "fold_equity": _fe_detail,
            # --- durable ground truth ------------------------------------
            # Captured at DECISION TIME so it survives independently of
            # /texas/recent-tables, which is a rolling 50-hand window and was
            # the only source for any of this. Three separate investigations
            # (the equity>0.82 sizing audit, the fold-equity scoping, and the
            # trajectory design) each stalled because the hands they needed had
            # already aged out of that window.
            #
            # pot/to_call make bet-to-pot ratio computable after the fact. That
            # ratio is the primary key any fold-equity lookup would need, and
            # it was previously recoverable from NOTHING: absent from the log
            # and absent from recent-tables alike.
            "pot": pot, "to_call": to_call,
            "bet_to_pot": round((amount - my_bet) / pot, 3)
                          if pot and action in ("bet", "raise", "all-in") and amount else None,
            # The sizing scheme's OWN fraction, before sized_bet/sized_raise
            # apply the looseness multiplier and before the raise-to arithmetic.
            #
            # `bet_to_pot` above is NOT comparable between bets and raises:
            # sized_raise returns a raise-TO amount (call_amount + pot*frac), so
            # the logged ratio is frac + to_call/pot. A raise facing a big bet
            # therefore logs a large value regardless of how it was sized -
            # observed live at 0.724 and 0.953 on a board where the scheme asked
            # for 0.561. Any corr(size, category) computed on bet_to_pot pools
            # two different quantities and does not measure the tell.
            "size_frac": (round(scheme_size(street, board_raw), 3)
                          if (SIZING_SCHEME_ENABLED
                              and action in ("bet", "raise", "all-in")
                              and scheme_size(street, board_raw) is not None)
                          else None),
            "board_texture": (board_texture(board_raw)[0]
                              if street != "Preflop" else None),
            # Our own hole cards ARE visible to us at decision time (opponents'
            # are null until showdown), so they never needed the snapshot at
            # all. Logging them closes our half of the ground-truth gap
            # permanently. See archive_recent_tables() for the opponent half.
            "hole": hole_raw, "board": board_raw,
            "shadow_brake": shadow,
            "stack_commit_frac": round((amount + already_committed_other_streets) / (my_total_committed + stack), 3)
                                  if stack and (my_total_committed + stack) > 0 else None,
        }
        return result
    except Exception:
        action = "fold" if to_call > 0 else "check"
        if allowed_names and action not in allowed_names:
            for cand in ("check", "call", "fold"):
                if cand in allowed_names:
                    action = cand
                    break
            else:
                action = next(iter(allowed_names), action)
        amount = call_amount if action == "call" else 0
        return {"action": action, "amount": int(amount), "message": "playing it safe",
                "_debug": {"note": "fallback path (parsing/computation error)",
                           "decided_by": "fallback_error_path"}}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# Opponent hole cards are null in every live payload - they exist ONLY in
# /texas/recent-tables, after the hand settles, and that endpoint is a rolling
# 50-hand window. At ~0.25 hands/min a hand ages out in roughly 3 hours, so any
# analysis run later than that has no opponent ground truth at all. That is the
# single constraint that has blocked the most investigations in this project.
#
# This appends each newly-settled table to a JSONL, deduped by table id, so the
# window is captured before it rolls. Append-only and idempotent: re-running or
# overlapping runs cannot corrupt it, only skip duplicates.
ARCHIVE_PATH = "C:/Users/billy/poker-arena/hand_archive.jsonl"
ARCHIVE_INTERVAL_S = 300
_archived_ids = None
_my_agent_id = None


def my_agent_id():
    """Our own agentId, derived once from /agent/me and cached.

    Derived rather than hardcoded: a pasted id is one more constant to go stale,
    and this project has been bitten twice by hand-maintained constants (log
    paths, the S17-named S18 competition id). The API is the authority.
    Returns "" on failure, which makes the caller's request 404 harmlessly
    rather than raising inside the archiver.
    """
    global _my_agent_id
    if _my_agent_id is None:
        try:
            s, r = call("GET", "/agent/me")
            _my_agent_id = (r or {}).get("id") or "" if s == 200 else ""
        except Exception:
            _my_agent_id = ""
    return _my_agent_id


def _load_archived_ids():
    ids = set()
    try:
        with open(ARCHIVE_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return ids


# Live-seen event streams, keyed by table id, merged into the archive at settle.
#
# WHY THIS EXISTS. /texas/recent-tables does NOT return `recentEvents` - the key
# is absent entirely from settled tables. The live poll DOES see it, and it is
# the only place it ever exists. Three separate investigations have now hit this
# wall: reconstructing aggression_score for the cmtcarsk/cmtd2ab0 busts is
# impossible because the events were seen at decision time and thrown away.
#
# `cumulative_aggression` is stateless over exactly this stream, so persisting
# it makes every future aggression-conditioned proposal testable against the
# hands that motivate the safeguards. It cannot recover the old busts, but it
# stops the same wall being hit a fourth time.
#
# Keeps the LONGEST stream seen per table: recentEvents grows as the hand
# progresses, so the last poll before settlement carries the most complete
# history. Bounded by dropping entries once archived.
_live_events = {}
_LIVE_EVENTS_MAX = 400

# --- 19 SHADOW LOGGER --------------------------------------------------------
# When AGGRESSION_FOLD refuses a pot_odds_call the hand ENDS, so the call's
# return is never observed. More runs cannot fix that - what is missing is a
# counterfactual, not a bigger sample. This records the refusal and then,
# at settlement, whatever the arena reveals about how the hand finished.
#
# OBSERVATION ONLY. It never changes the action taken, the threshold, or the
# entry condition. If this code raises, play continues unaffected.
#
# WHAT IT CANNOT DELIVER. When our fold ends the action the board never runs
# out and there is nothing to compare against; those records carry
# resolved=false and are expected to be the majority. Only the subset where the
# hand continued is worth anything - and even there the runout is NOT "what we
# would have won": our continued presence would have changed the hand. It
# bounds the question, it does not answer it.
SHADOW_PATH = "shadow_aggression_fold.jsonl"
_shadow_pending = {}
_SHADOW_PENDING_MAX = 400


def _shadow_append(rec):
    with open(SHADOW_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def shadow_record_fold(hand_id, rec):
    """Persist a refusal IMMEDIATELY. Crash-safe by design: this process has
    been killed mid-run repeatedly (reboot, Windows Update, flat battery), and
    a record held in memory until settlement would be lost every time."""
    try:
        if not hand_id:
            return
        rec = dict(rec)
        rec["hand_id"] = hand_id
        rec["stage"] = "fold"
        rec["resolved"] = False
        rec["competition_id"] = COMPETITION_ID
        rec["logged_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _shadow_append(rec)
        _shadow_pending[hand_id] = True
        if len(_shadow_pending) > _SHADOW_PENDING_MAX:
            for k in list(_shadow_pending)[:len(_shadow_pending) - _SHADOW_PENDING_MAX]:
                _shadow_pending.pop(k, None)
    except Exception:
        pass


def shadow_record_settlement(table):
    """Append the settlement half for a hand we refused. Joined on hand_id,
    which IS the table id - verified: handCount == 1 on all 5019 archived rows,
    so a table is exactly one hand and the join is 1:1.

    winners/payoutChips are taken VERBATIM from the arena. Principle 15: these
    are ground truth for MONEY. Ranking the hands ourselves answers a different
    question and is deliberately not done here."""
    try:
        tid = table.get("id") or table.get("tableId")
        if not tid or tid not in _shadow_pending:
            return
        board = table.get("boardCards") or []
        payouts = {}
        for st in (table.get("seats") or []):
            if st.get("payoutChips") is not None:
                payouts[str(st.get("seatNumber"))] = st.get("payoutChips")
        _shadow_append({
            "hand_id": tid,
            "stage": "settled",
            "resolved": True,
            # Ran out vs merely settled are different facts - a hand can end on
            # the flop with everyone folding. Kept as separate fields.
            "board_ran_out": len(board) >= 5,
            "final_board": board,
            "winners": table.get("winners"),
            "payout_chips": payouts,
            "chip_delta": table.get("chipDelta"),
            "settled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        _shadow_pending.pop(tid, None)
    except Exception:
        pass



def remember_events(table):
    """Stash a table's recentEvents. Never raises - this is bookkeeping."""
    try:
        tid = table.get("tableId") or table.get("id")
        events = table.get("recentEvents")
        if not tid or not events:
            return
        prev = _live_events.get(tid)
        if prev is None or len(events) > len(prev):
            _live_events[tid] = events
        # Cheap bound. Entries are removed as tables settle; this only matters
        # if a run sees far more tables than it archives.
        if len(_live_events) > _LIVE_EVENTS_MAX:
            for k in list(_live_events)[:len(_live_events) - _LIVE_EVENTS_MAX]:
                _live_events.pop(k, None)
    except Exception:
        pass


def archive_recent_tables():
    """Append newly-settled tables (with hole cards) to the durable archive.

    Merges in the live-captured `recentEvents` for each table, which the
    settled-table endpoint does not return.

    Returns the number of new hands captured. Never raises - archiving must
    never be able to interrupt play."""
    global _archived_ids
    try:
        if _archived_ids is None:
            _archived_ids = _load_archived_ids()
        status, resp = call("GET", "/texas/recent-tables",
                            query={"competitionId": COMPETITION_ID, "limit": 50})
        if status != 200:
            return 0
        new = [t for t in (resp.get("data") or [])
               if t.get("id") and t["id"] not in _archived_ids]
        if not new:
            return 0
        # Per-hand chip result, captured at settle.
        #
        # WHY. /agent/{id}/replays is the ONLY source of a true per-hand
        # chipDelta, and it is a rolling 50-table window - deltas age out
        # permanently within a few hours. The archive's own seat fields cannot
        # substitute: `totalCommittedChips` is None on 100% of 2228 archived
        # hands, so the obvious `payoutChips - totalCommittedChips` proxy
        # silently computes `payout - 0` and reports pure winnings as if it were
        # net. That produced a +4788 "net" on a stretch that actually LOST
        # chips. Without this capture, any loss attribution more than a few
        # hours old is unanswerable - which is exactly what happened trying to
        # explain a 2171-chip decline over 570 hands.
        deltas = {}
        try:
            dstat, dresp = call("GET", "/agent/%s/replays" % my_agent_id(),
                                query={"competitionId": COMPETITION_ID, "limit": 50})
            if dstat == 200 and isinstance(dresp, list):
                deltas = {x.get("tableId"): x.get("chipDelta")
                          for x in dresp if x.get("tableId") is not None}
        except Exception:
            deltas = {}
        with open(ARCHIVE_PATH, "a", encoding="utf-8") as f:
            for t in new:
                if t["id"] in deltas and deltas[t["id"]] is not None:
                    t = dict(t)
                    t["chipDelta"] = deltas[t["id"]]
                    t["_delta_source"] = "replays"
                if not t.get("recentEvents"):
                    ev = _live_events.get(t["id"])
                    if ev:
                        t = dict(t)
                        t["recentEvents"] = ev
                        t["_events_source"] = "live_poll"
                f.write(json.dumps(t) + "\n")
                _archived_ids.add(t["id"])
                _live_events.pop(t["id"], None)
                # 19 shadow join. Reads the SAME settled-table payload the
                # archive writes, so the two cannot drift apart.
                shadow_record_settlement(t)
        return len(new)
    except Exception:
        return 0


def finalize(actions_taken, reason):
    """Flush archive + write the session-log entry. Safe to call on ANY exit.

    Bug this fixes: the abort path (MAX_CONSECUTIVE_FAILURES) was a bare
    `return`, skipping the archive flush, the FINAL participant log, AND the
    live_session_log.jsonl write. Confirmed on run v24, which played 21 hands
    and took 40 actions before aborting on a DNS fault and left NO session-log
    entry at all - the run is invisible in the session history, and any hands
    that settled after its last 5-minute archive tick were never captured.

    Every failure is swallowed: cleanup must never mask the original reason for
    exiting, and on a network-fault abort these calls will usually fail anyway.
    """
    try:
        n = archive_recent_tables()
        log(f"hand archive: captured {n} hand(s) on exit ({reason})")
    except Exception:
        pass
    participant = None
    try:
        # poll_pending, so the final snapshot also works in benchmark mode.
        # Defined below main() but only ever called at run end.
        _s, final = poll_pending()
        participant = final.get("participant")
        log(f"FINAL participant: {participant}")
    except Exception:
        log("FINAL participant: unavailable (call failed during cleanup)")
    log(f"actions_taken={actions_taken}")
    try:
        with open("C:/Users/billy/poker-arena/live_session_log.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "participant": participant,
                                "actions_taken": actions_taken,
                                "exit_reason": reason}) + chr(10))
    except Exception:
        pass


# ---------------------------------------------------------------- benchmark
# Benchmark competitions (Eval S1) are entered through /texas/benchmark/start
# rather than the matchmaking lobby, and polled via /texas/benchmark/status,
# which returns {match, table, participant} instead of pending-actions'
# {tables, activeTables, lobby, participant}.
#
# This is plumbing only: normalize the benchmark envelope into the pending
# -actions shape and the entire decision path downstream is untouched.
#
# The response shape of /start and the populated shape of /status could NOT be
# learned without starting a scored run (no OpenAPI spec, no skill file over the
# API, no public docs, no attempts endpoint). So everything here is written to
# tolerate an unknown shape and to LOG THE RAW PAYLOAD the first time it sees
# one - the first run is partly a shape-discovery run, and it should not be
# necessary to do a second one to find out what came back.
BENCHMARK_MODE = False
_bench_shape_logged = set()


def _log_shape(label, payload):
    """Log a payload's structure once per label. Shape discovery, not spam."""
    if label in _bench_shape_logged:
        return
    _bench_shape_logged.add(label)
    try:
        if isinstance(payload, dict):
            desc = {k: type(v).__name__ for k, v in payload.items()}
            log(f"=== BENCHMARK SHAPE [{label}] keys={desc}")
        else:
            desc = type(payload).__name__
            log(f"=== BENCHMARK SHAPE [{label}] type={desc}")
        log(f"=== BENCHMARK RAW [{label}] {json.dumps(payload, default=str)[:1800]}")
    except Exception:
        log(f"=== BENCHMARK RAW [{label}] <unserializable> {str(payload)[:800]}")


def benchmark_start():
    """POST /texas/benchmark/start. -> (ok, response).

    Request shape is confirmed: competitionId is the only required property
    (probed via a 400 on an empty body and a 404 on an unknown id, neither of
    which starts anything).
    """
    status, resp = call("POST", "/texas/benchmark/start",
                        {"competitionId": COMPETITION_ID})
    log(f"benchmark/start -> {status} {str(resp)[:400]}")
    _log_shape("start", resp)
    if status == 402:
        log("benchmark/start requires payment - this bot never pays, stopping")
        return False, resp
    return status == 200, resp


def _table_is_actionable(table):
    """Is this table waiting on US right now?

    pending-actions pre-filters to tables awaiting our action; benchmark/status
    returns the table regardless. Without this check the loop would re-decide
    and re-POST the same table in a tight spin while it is the panel's turn.
    """
    if not isinstance(table, dict):
        return False
    allowed = table.get("allowedActions") or {}
    if not (allowed.get("availableActions") or []):
        return False
    acting = table.get("actingSeatNumber")
    mine = table.get("selfSeatNumber")
    if acting is not None and mine is not None and acting != mine:
        return False
    return True


def benchmark_poll():
    """GET /texas/benchmark/status, normalized to the pending-actions envelope.

    -> (status, {tables, activeTables, lobby, participant, _match})
    """
    status, resp = call("GET", "/texas/benchmark/status",
                        query={"competitionId": COMPETITION_ID})
    if status != 200 or not isinstance(resp, dict):
        return status, resp
    if resp.get("table") is not None:
        _log_shape("status.table", resp.get("table"))
    if resp.get("match") is not None:
        _log_shape("status.match", resp.get("match"))
    table = resp.get("table")
    # Tolerate a list as well as a single object - the populated shape is
    # unverified, and guessing wrong should degrade to "nothing to do", never
    # to a crash mid-run.
    tables = table if isinstance(table, list) else ([table] if table else [])
    actionable = [t for t in tables if _table_is_actionable(t)]
    return 200, {
        "tables": actionable,
        "activeTables": [t for t in tables if isinstance(t, dict)],
        "lobby": {},
        "participant": resp.get("participant") or {},
        "_match": resp.get("match"),
    }


def perform_rebuy(pre_chips=None):
    """POST /texas/rebuy and log the STACK RESET marker. -> "ok" | "paid" | "fail".

    WHY THIS EXISTS AT THE JOIN PATH. v9 had exactly this bug mid-run: it
    hammered plain /texas/join into a permanent insufficient_chips 409 instead
    of ever calling rebuy, and burned 3h44m of a 4-hour run. That was fixed
    where it was found - inside the hand loop, keyed off chipState == "busted" -
    and NOT at startup, where no bust has happened yet because no hand has been
    played. On 2026-10-07 a run began on a 1-chip stack, every join returned
    insufficient_chips, and v170/v171 each spent five backoff retries and
    aborted with zero decisions; the chain reached 2/3 on its terminal
    condition. Same defect class, different entry point - fixing the instance
    rather than the class let it come back (PRINCIPLES 17).

    The mid-run caller is deliberately left alone rather than refactored onto
    this helper: it is the one rebuy path with five live firings behind it, and
    an operational fix is the wrong moment to disturb working code.
    """
    rstatus, rresp = call("POST", "/texas/rebuy", {"competitionId": COMPETITION_ID})
    log(f"rebuy -> {rstatus} {rresp}")
    if rstatus == 402:
        log("rebuy requires payment - this bot never pays automatically, stopping")
        return "paid"
    if rstatus != 200:
        log(f"rebuy failed unexpectedly ({rstatus}), not retrying the endpoint")
        return "fail"
    _rp = (rresp or {}).get("participant") or rresp or {}
    log("=== STACK RESET (rebuy #%s): %s -> %s chips. Hands before and "
        "after this line are SEPARATE samples - do not pool them in any "
        "chip-denominated statistic."
        % (_rp.get("rebuyCount"), pre_chips, _rp.get("totalChips")))
    return "ok"


def poll_pending():
    """Poll for tables awaiting our action, in whichever mode we are in."""
    if BENCHMARK_MODE:
        return benchmark_poll()
    return call("GET", "/texas/pending-actions",
                query={"competitionId": COMPETITION_ID})


def main():
    duration_s = int(sys.argv[1]) if len(sys.argv) > 1 else 420
    start = time.time()

    # Refuse before the first network call. Joining a paid competition spends
    # real money and is a human's decision, not a config change that happens to
    # point here.
    if COMPETITION_ID in PAID_COMPETITIONS:
        log(f"REFUSING to start: {COMPETITION_ID} is a PAID competition "
            f"({PAID_COMPETITIONS[COMPETITION_ID]}). Entering it spends real "
            f"funds. This bot never pays. If this is genuinely intended, a human "
            f"must make that call deliberately - not by repointing COMPETITION_ID.")
        return

    global BENCHMARK_MODE
    joined_rebuys = 0
    for attempt in range(5):
        status, resp = call("POST", "/texas/join", {"competitionId": COMPETITION_ID})
        log(f"join -> {status} {resp}")
        # Which entry path this competition uses is DERIVED from the arena's own
        # answer rather than kept in a list here. A hardcoded set of benchmark
        # ids is one more thing to go stale (see the twice-stale log paths);
        # this cannot disagree with the server because it comes from it.
        if (resp or {}).get("error") == "benchmark_lobby_unavailable":
            BENCHMARK_MODE = True
            log("benchmark competition detected - entering via /texas/benchmark/start "
                "and polling /texas/benchmark/status instead of the lobby")
            ok, _bresp = benchmark_start()
            if not ok:
                log("benchmark/start failed; aborting rather than polling a match "
                    "that was never created")
                return
            break
        kind = classify_join(status, resp)
        if kind == "ok":
            break
        if kind == "seated":
            log(f"already seated/queued ({resp.get('error')}); polling pending-actions instead of retrying join")
            break
        if kind == "terminal":
            log(f"join failed with a non-recoverable error ({resp.get('error')}); "
                f"aborting instead of retrying - if this is competition_not_active the season has "
                f"ENDED and COMPETITION_ID needs updating to the current one "
                f"(GET /competition/list-active)")
            return
        if kind == "rebuy":
            # The arena's own message says "Call /texas/rebuy if rebuy is
            # available" - so call it, rather than retrying a join that cannot
            # succeed until the stack is restored. Capped at one rebuy per
            # launch: if a fresh 1000 chips still will not let us in, the cause
            # is not the stack and looping would just spend rebuys.
            if joined_rebuys:
                log("join still refused after a rebuy - not rebuying again, "
                    "the stack is not the blocker here; aborting")
                return
            joined_rebuys += 1
            _r = perform_rebuy()
            if _r != "ok":
                return
            continue
        backoff = min(60, 2 ** (attempt + 2))
        log(f"join failed ({kind}), retrying in {backoff}s")
        time.sleep(backoff)
    else:
        log("could not join after retries, aborting")
        return

    hands_started_seen = set()
    actions_taken = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 8
    consecutive_action_failures = 0
    MAX_ACTION_FAILURES = 8
    # Log the loaded constants so a restart's behaviour is verifiable from the
    # log itself rather than assumed from the file on disk - a running process
    # keeps whatever it read at launch, and a stale process silently ignoring a
    # shipped fix has already cost chips once.
    log(f"config: made_hand_exempt>={STACK_VELOCITY_MADE_HAND_EXEMPT} "
        f"velocity_caps={STACK_VELOCITY_CAP_BY_STREET} "
        f"shadow_mode={STACK_VELOCITY_SHADOW_MODE} competition={COMPETITION_ID}")
    # Greppable competition banner. The config line above already carries the id,
    # but an id is not self-describing: a later reader (or script) pooling this
    # log with a Playground one would be mixing a PVE fixed panel with a live
    # 6-max pool. Analysis splits on "=== COMPETITION".
    # Always emit the banner, whatever the competition. It was briefly written
    # to fire only for Eval S1, which is backwards: a log with no banner is
    # exactly the one a later script would pool with something incomparable.
    _c = COMPETITION_LABELS.get(COMPETITION_ID, "UNKNOWN competition")
    log(f"=== COMPETITION: {_c} [{COMPETITION_ID}]. Numbers are NOT comparable "
        f"across competitions - PFR, VPIP, call:raise and branch B/C mix are all "
        f"pool-dependent. Only mechanism checks transfer between venues.")
    n = archive_recent_tables()
    log(f"hand archive: captured {n} hand(s) at start")
    last_archive = time.time()

    while time.time() - start < duration_s:
        status, resp = poll_pending()
        if status != 200:
            consecutive_failures += 1
            backoff = min(60, 2 ** consecutive_failures)
            log(f"pending-actions error {status} {resp} (failure #{consecutive_failures}, backing off {backoff}s)")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log(f"giving up after {consecutive_failures} consecutive failures, aborting round early")
                finalize(actions_taken, "poll_failures")
                return
            time.sleep(backoff)
            continue
        consecutive_failures = 0

        if time.time() - last_archive >= ARCHIVE_INTERVAL_S:
            n = archive_recent_tables()
            last_archive = time.time()
            if n:
                log(f"hand archive: captured {n} new hand(s)")

        tables = resp.get("tables") or []
        active_tables = resp.get("activeTables") or []
        lobby = resp.get("lobby") or {}
        participant = resp.get("participant") or {}

        # Capture event streams from EVERY table we see, not just the one we
        # act on: /texas/recent-tables drops recentEvents at settle, so this
        # poll is the only place they exist (see remember_events).
        for _t in list(tables) + list(active_tables):
            remember_events(_t)

        if tables:
            tables_sorted = sorted(tables, key=lambda t: t.get("actionDeadlineAt") or float("inf"))
            table = tables_sorted[0]
            decision = decide_live(table)
            debug = decision.pop("_debug", {})
            body = {"tableId": table.get("tableId") or table.get("id"), **decision}
            status, action_resp = call("POST", "/texas/action", body)
            actions_taken += 1
            if status != 200:
                # This branch has NO sleep - deliberately, so we act fast on a
                # live deadline. But that means a persistently failing action
                # against a table that stays in `tables` is an unthrottled hot
                # loop. Every other retry path in this file has a counter and a
                # give-up (poll: MAX_CONSECUTIVE_FAILURES; join: classify_join);
                # this one had neither. Latent rather than observed - only 3
                # action failures across all runs to date, all isolated - but
                # it is the same shape as the 3,087-call join incident.
                consecutive_action_failures += 1
                log(f"action FAILED {status} {action_resp} "
                    f"(failure #{consecutive_action_failures}) body={body} debug={debug}")
                if consecutive_action_failures >= MAX_ACTION_FAILURES:
                    log(f"giving up after {consecutive_action_failures} consecutive "
                        f"action failures, aborting round early")
                    finalize(actions_taken, "action_failures")
                    return
                time.sleep(min(30, 2 ** consecutive_action_failures))
            else:
                consecutive_action_failures = 0
                log(f"table={body['tableId'][:8]} street={table.get('street')} action={decision['action']} amount={decision.get('amount',0)} -> ok debug={debug}")
        elif not active_tables and not lobby.get("position"):
            chip_state = participant.get("chipState")
            # No code path ever called /texas/rebuy - a genuinely busted agent
            # would just sit here doing nothing for the rest of duration_s.
            # S16's rules advertise free, unlimited rebuys on bust (rebuy-status
            # confirms maxRebuys/rebuysRemaining are both 9999 for this
            # competition). Only ever sent with a bare competitionId - never a
            # txHash or paymentReference, so this can't accidentally trigger a
            # paid rebuy; if the server ever responds 402 (payment required)
            # this bot stops rather than attempting to pay.
            #
            # v9.1: chipState == "busted" is the ONLY correct trigger - trust
            # it alone, per the schema's own description ("Use this instead
            # of inferring from bankrollChips/totalChips"). v9's first attempt
            # additionally required totalChips <= 0, on the wrong assumption
            # that "busted" means exactly zero; live it was chipState="busted"
            # with totalChips=1 (a sub-big-blind remainder), so that extra
            # condition never matched and the agent spent 3h44m of a 4-hour
            # run hammering plain /texas/join into a permanent
            # insufficient_chips 409 instead of ever calling rebuy.
            if chip_state == "busted":
                _pre = participant.get("totalChips")
                rstatus, rresp = call("POST", "/texas/rebuy", {"competitionId": COMPETITION_ID})
                log(f"rebuy -> {rstatus} {rresp}")
                # Explicit, greppable marker. A rebuy resets the stack mid-run,
                # so every chip-denominated statistic has a discontinuity here:
                # hands either side are NOT one sample. Analysis splits on
                # "=== STACK RESET"; without a marker this reads later as an
                # unexplained jump in the chip line.
                if rstatus == 200:
                    # The rebuy response nests everything under "participant";
                    # reading rebuyCount/totalChips off the top level printed
                    # "rebuy #None: 0 -> None chips" on the first real firing
                    # (v39, 2026-09-03). The marker was written blind because
                    # the rebuy path had never executed - a reminder that an
                    # observability line is itself unverified code until it runs.
                    # Falls back to the top level in case the shape ever changes.
                    _rp = (rresp or {}).get("participant") or rresp or {}
                    log("=== STACK RESET (rebuy #%s): %s -> %s chips. Hands before and "
                        "after this line are SEPARATE samples - do not pool them in any "
                        "chip-denominated statistic."
                        % (_rp.get("rebuyCount"), _pre, _rp.get("totalChips")))
                if rstatus == 402:
                    log("rebuy requires payment - this bot never pays automatically, stopping")
                    return
                if rstatus != 200:
                    log(f"rebuy failed unexpectedly ({rstatus}), stopping rather than hammering the endpoint")
                    return
                status, join_resp = call("POST", "/texas/join", {"competitionId": COMPETITION_ID})
                log(f"re-join after rebuy -> {status} {join_resp}")
            else:
                status, join_resp = call("POST", "/texas/join", {"competitionId": COMPETITION_ID})
                log(f"re-join -> {status} {join_resp}")
            # Same classification as the initial join. Without this, ANY
            # unrecognised re-join failure loops here every 2s for the rest of
            # duration_s: one run logged 3,087 consecutive competition_not_active
            # re-joins over ~2h after S16 ended mid-run.
            if classify_join(status, join_resp) == "terminal":
                log(f"re-join hit a non-recoverable error ({(join_resp or {}).get('error')}); "
                    f"stopping rather than looping. If this is competition_not_active the season "
                    f"has ENDED mid-run - update COMPETITION_ID (GET /competition/list-active).")
                break
            time.sleep(2)
        else:
            time.sleep(2)

    log("time budget spent, leaving")
    status, resp = call("POST", "/texas/leave", {"competitionId": COMPETITION_ID})
    log(f"leave -> {status} {resp}")

    drain_failures = 0
    drain_deadline = time.time() + 180
    while time.time() < drain_deadline:
        status, resp = poll_pending()
        if status != 200:
            drain_failures += 1
            if drain_failures >= 5:
                log("too many drain failures, giving up on drain")
                break
            time.sleep(min(30, 2 ** drain_failures))
            continue
        drain_failures = 0
        tables = resp.get("tables") or []
        if tables:
            table = tables[0]
            decision = decide_live(table)
            debug = decision.pop("_debug", {})
            body = {"tableId": table.get("tableId") or table.get("id"), **decision}
            call("POST", "/texas/action", body)
            actions_taken += 1
            log(f"draining table={body['tableId'][:8]} street={table.get('street')} action={decision['action']} amount={decision.get('amount',0)} debug={debug}")
            continue
        runner = resp.get("runner") or {}
        if runner.get("canStopSafely"):
            log("canStopSafely=true, done")
            break
        time.sleep(2)

    finalize(actions_taken, "completed")


if __name__ == "__main__":
    main()
