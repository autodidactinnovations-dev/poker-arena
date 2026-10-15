import random
import sys

sys.path.insert(0, "C:/Users/billy/poker-arena")
import strategy as S

RANKS = "23456789TJQKA"
SUITS = "shdc"


def fresh_deck():
    return [r + s for r in RANKS for s in SUITS]


def make_seats(n, self_idx, hole, board, to_call, current_bet_total, sb_seat, bb_seat,
                stacks=None, statuses=None, agent_ids=None):
    seats = []
    deck = fresh_deck()
    used = set(hole) | set(board)
    deck = [c for c in deck if c not in used]
    random.shuffle(deck)
    for i in range(n):
        seat_num = i + 1
        if statuses and statuses[i] == "Folded":
            hc = None
        elif i == self_idx:
            hc = hole
        else:
            hc = None  # hidden opponent cards (realistic)
        seats.append({
            "seatId": f"s{seat_num}",
            "seatNumber": seat_num,
            "agentId": (agent_ids[i] if agent_ids else f"agent{seat_num}"),
            "agentName": f"Agent{seat_num}",
            "agentHandle": f"agent{seat_num}",
            "status": (statuses[i] if statuses else "Active"),
            "stackChips": (stacks[i] if stacks else 500),
            "currentBetChips": (to_call + current_bet_total if i == self_idx else current_bet_total),
            "totalCommittedChips": current_bet_total,
            "payoutChips": None,
            "holeCards": hc,
        })
    return seats


def make_recent_events(sb_seat, bb_seat, sb_chips, bb_chips, extra=None):
    events = [
        {"id": "e1", "sequence": 1, "type": "BlindPosted", "street": "Preflop",
         "occurredAt": 1, "summary": {"action": None, "amount": sb_chips, "toAmount": None,
                                       "reasoning": None, "cards": None, "boardCards": None,
                                       "seatNumber": sb_seat, "agentName": f"Agent{sb_seat}"}},
        {"id": "e2", "sequence": 2, "type": "BlindPosted", "street": "Preflop",
         "occurredAt": 2, "summary": {"action": None, "amount": bb_chips, "toAmount": None,
                                       "reasoning": None, "cards": None, "boardCards": None,
                                       "seatNumber": bb_seat, "agentName": f"Agent{bb_seat}"}},
    ]
    if extra:
        events.extend(extra)
    return events


def make_allowed(to_call, stack, my_bet, street, min_raise_to=None, min_bet=None,
                  max_commit=None, can_bet=None, can_raise=None, degenerate_min_raise=False):
    can_check = to_call <= 0
    can_call = to_call > 0
    if can_bet is None:
        can_bet = to_call <= 0
    if can_raise is None:
        can_raise = to_call > 0
    call_to_amount = my_bet + to_call
    max_c = max_commit if max_commit is not None else stack + my_bet
    if min_bet is None:
        min_bet = 2
    if min_raise_to is None:
        min_raise_to = call_to_amount + 2
    if degenerate_min_raise:
        min_raise_to = max_c + 1000  # forces below_true_min -> allin path
    avail = []
    if to_call > 0:
        avail.append("fold")
    else:
        avail.append("check")
        avail.append("fold")
    if can_call:
        avail.append("call")
    if can_bet:
        avail.append("bet")
    if can_raise:
        avail.append("raise")
    avail.append("all-in")
    return {
        "canFold": True, "canCheck": can_check, "canCall": can_call,
        "canBet": can_bet, "canRaise": can_raise,
        "callAmount": to_call, "callChips": to_call, "callToAmount": call_to_amount,
        "minBet": min_bet if can_bet else None,
        "minRaiseTo": min_raise_to if can_raise else None,
        "maxCommit": max_c,
        "allInToAmount": max_c,
        "betRange": {"min": min_bet, "max": max_c} if can_bet else None,
        "raiseRange": {"min": min_raise_to, "max": max_c} if can_raise else None,
        "canAllIn": True,
        "availableActions": avail,
        "amountSemantics": "toAmount",
        "amountHint": "total chips committed on this street",
        "reasoningRequired": random.random() < 0.5,
        "actionHint": "test",
    }


def random_hole():
    deck = fresh_deck()
    random.shuffle(deck)
    return deck[:2]


def random_board(n):
    deck = fresh_deck()
    random.shuffle(deck)
    return deck[:n]


def run_case(n_players, street, self_idx, to_call, degenerate=False, no_events=False,
             stack_override=None, empty_holecards=False):
    hole = random_hole()
    board_n = {"Preflop": 0, "Flop": 3, "Turn": 4, "River": 5}[street]
    used = set(hole)
    deck = [c for c in fresh_deck() if c not in used]
    random.shuffle(deck)
    board = deck[:board_n]

    sb_seat = 1
    bb_seat = 2 if n_players > 2 else 1
    if n_players == 2:
        sb_seat, bb_seat = 1, 2

    stack = stack_override if stack_override is not None else random.choice([2, 5, 50, 500, 5000])
    my_bet_this_street = 0
    seats = make_seats(n_players, self_idx, ([] if empty_holecards else hole), board, to_call,
                        my_bet_this_street, sb_seat, bb_seat, stacks=[stack] * n_players)

    table = {
        "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
        "status": "Active", "street": street,
        "potChips": random.choice([2, 10, 100, 1000]),
        "currentBet": to_call,
        "minRaiseTo": None,
        "startedAt": 1, "endedAt": None, "countdownEndsAt": None,
        "actionDeadlineAt": 999999,
        "currentSeatNumber": seats[self_idx]["seatNumber"],
        "boardCards": board,
        "smallBlindChips": 1, "bigBlindChips": 2, "buyInChips": 2,
        "winners": [], "seats": seats,
        "actingSeatNumber": seats[self_idx]["seatNumber"],
        "selfSeatNumber": seats[self_idx]["seatNumber"],
        "allowedActions": make_allowed(to_call, stack, my_bet_this_street, street,
                                        degenerate_min_raise=degenerate),
        "recentEvents": ([] if no_events else make_recent_events(sb_seat, bb_seat, 1, 2)),
    }
    return S.decide(table)


def validate_result(res, table):
    assert isinstance(res, dict), f"result not dict: {res}"
    assert "action" in res and "amount" in res and "message" in res, res
    allowed = table["allowedActions"]
    avail = set(a.lower() for a in allowed["availableActions"])
    # sandbox contract uses 'allin' not 'all-in'
    avail_norm = set()
    for a in avail:
        avail_norm.add("allin" if a == "all-in" else a)
    assert res["action"] in avail_norm, f"illegal action {res['action']} not in {avail_norm}"
    amt = res["amount"]
    assert isinstance(amt, int), f"amount not int: {amt}"
    if res["action"] in ("bet", "raise", "allin"):
        assert amt >= 1, f"non-positive sizing amount: {amt}"
        assert amt <= allowed["maxCommit"], f"amount {amt} exceeds maxCommit {allowed['maxCommit']}"


def main():
    random.seed(1234)
    total = 0
    errors = []
    for trial in range(20000):
        n_players = random.choice([2, 3, 4, 5, 6])
        street = random.choice(["Preflop", "Flop", "Turn", "River"])
        self_idx = random.randrange(n_players)
        to_call = random.choice([0, 0, 2, 5, 20, 100, 4999])
        degenerate = random.random() < 0.05
        no_events = random.random() < 0.1
        stack_override = random.choice([None, None, 1, 2])
        empty_holecards = random.random() < 0.02

        total += 1
        hole = random_hole()
        board_n = {"Preflop": 0, "Flop": 3, "Turn": 4, "River": 5}[street]
        used = set(hole)
        deck = [c for c in fresh_deck() if c not in used]
        random.shuffle(deck)
        board = deck[:board_n]
        sb_seat = 1
        bb_seat = 2 if n_players > 2 else (2 if n_players == 2 else 2)
        stack = stack_override if stack_override is not None else random.choice([2, 5, 50, 500, 5000])
        seats = make_seats(n_players, self_idx, ([] if empty_holecards else hole), board, to_call,
                            0, sb_seat, bb_seat, stacks=[stack] * n_players)
        table = {
            "id": "t1", "tableId": "t1", "tableNumber": 1, "competitionId": "c1",
            "status": "Active", "street": street,
            "potChips": random.choice([2, 10, 100, 1000]),
            "currentBet": to_call,
            "minRaiseTo": None,
            "startedAt": 1, "endedAt": None, "countdownEndsAt": None,
            "actionDeadlineAt": 999999,
            "currentSeatNumber": seats[self_idx]["seatNumber"],
            "boardCards": board,
            "smallBlindChips": 1, "bigBlindChips": 2, "buyInChips": 2,
            "winners": [], "seats": seats,
            "actingSeatNumber": seats[self_idx]["seatNumber"],
            "selfSeatNumber": seats[self_idx]["seatNumber"],
            "allowedActions": make_allowed(to_call, stack, 0, street, degenerate_min_raise=degenerate),
            "recentEvents": ([] if no_events else make_recent_events(sb_seat, bb_seat, 1, 2)),
        }
        try:
            res = S.decide(table)
            validate_result(res, table)
        except AssertionError as e:
            errors.append((trial, str(e), table))
        except Exception as e:
            errors.append((trial, f"EXCEPTION {type(e).__name__}: {e}", table))

    print(f"ran {total} trials, {len(errors)} errors")
    for trial, msg, table in errors[:10]:
        print("---")
        print(trial, msg)
        print("street", table["street"], "n_players", len(table["seats"]),
              "to_call", table["currentBet"], "self_seat", table["selfSeatNumber"])

    # sanity: opponent model accumulated something over many trials
    print("opponent stats tracked for", len(S._OPPONENT_STATS), "agent ids")
    print("seen event ids:", len(S._SEEN_EVENT_IDS))

    if errors:
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
