from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.domain.gameplay.exceptions import EngineValidationError

Color = str  # "red" | "blue" | "green" | "yellow"


# --- Board constants (standard Ludo) ---
# We model the main ring as indices 0..51 (52 squares).
# Each color has:
# - a start square on the ring where pieces enter on rolling 6
# - an "entry" square on the ring that leads into the home stretch
# - a home stretch of 6 squares indexed 0..5 (5 is the final home)
#
# Captures happen on the ring only and are blocked on "safe squares".
SAFE_SQUARES_RING = {0, 8, 13, 21, 26, 34, 39, 47}  # includes starts + star/safe squares


START_RING_INDEX: Dict[Color, int] = {
    "red": 0,
    "blue": 13,
    "green": 26,
    "yellow": 39,
}

HOME_ENTRY_RING_INDEX: Dict[Color, int] = {
    "red": 50,
    "blue": 11,
    "green": 24,
    "yellow": 37,
}


@dataclass(frozen=True)
class PiecePos:
    """
    Canonical piece position.

    type:
      - "yard": not yet in play
      - "ring": on the main 52-square ring at index 0..51
      - "home": in the color's home stretch at index 0..5
      - "finished": reached final home (all movement complete)
    """

    type: str
    index: Optional[int] = None


@dataclass(frozen=True)
class Move:
    """A single piece movement decision for a turn."""

    piece_index: int  # 0..3
    from_pos: PiecePos
    to_pos: PiecePos
    captures: List[Tuple[Color, int]]  # list of (captured_color, captured_piece_index)


@dataclass(frozen=True)
class LegalMoves:
    """Computed legal moves for a dice roll for a given player."""

    dice: int
    moves: List[Move]


def _ring_advance(start: int, steps: int) -> int:
    return (int(start) + int(steps)) % 52


def _is_safe_ring_square(idx: int) -> bool:
    return int(idx) in SAFE_SQUARES_RING


def _pos_from_state(piece: dict) -> PiecePos:
    ptype = str(piece.get("type"))
    if ptype == "yard":
        return PiecePos(type="yard", index=None)
    if ptype == "finished":
        return PiecePos(type="finished", index=None)
    if ptype == "ring":
        return PiecePos(type="ring", index=int(piece.get("index")))
    if ptype == "home":
        return PiecePos(type="home", index=int(piece.get("index")))
    # default
    return PiecePos(type="yard", index=None)


def _pos_to_state(pos: PiecePos) -> dict:
    if pos.type in ("yard", "finished"):
        return {"type": pos.type}
    return {"type": pos.type, "index": int(pos.index or 0)}


def _piece_at_ring(state: dict, *, ring_idx: int) -> List[Tuple[Color, int]]:
    """
    Return list of pieces on a given ring square.

    Returns:
        list[(color, piece_index)]
    """
    res: List[Tuple[Color, int]] = []
    pieces: dict = state.get("pieces") or {}
    for color, plist in pieces.items():
        for i, p in enumerate(plist or []):
            pos = _pos_from_state(p)
            if pos.type == "ring" and int(pos.index or -1) == int(ring_idx):
                res.append((str(color), int(i)))
    return res


def _validate_state_shape(state: dict) -> None:
    if not isinstance(state, dict):
        raise EngineValidationError("State must be a dict")
    if "pieces" not in state or not isinstance(state.get("pieces"), dict):
        raise EngineValidationError("State missing pieces")
    for color in ("red", "blue", "green", "yellow"):
        plist = (state["pieces"].get(color) or [])
        if len(plist) != 4:
            raise EngineValidationError(f"State pieces[{color}] must have 4 pieces")


def _color_order(players: List[dict]) -> List[Color]:
    # Stable order by seat_no (created earlier).
    ordered = sorted(players, key=lambda p: int(p.get("seat_no", 0)))
    return [str(p.get("color")) for p in ordered]


# PUBLIC_INTERFACE
def start_match_state(*, players: List[dict], mode: str = "classic") -> dict:
    """
    Create the canonical initial match state.

    Args:
        players: list of match player dicts with at least {user_id, seat_no, color}
        mode: game mode

    Returns:
        dict: canonical state to store in Redis/DB turn history.

    State format (versioned):
        {
          "v": 1,
          "mode": "classic",
          "turn_no": 1,
          "current_color": "red",
          "dice": null,
          "dice_rolled_by": null,
          "players": [...],
          "pieces": {color: [PiecePos, PiecePos, PiecePos, PiecePos]},
          "winners": [],  # list of colors who finished (for future ranking)
        }
    """
    colors = _color_order(players)
    # Default: first seat begins.
    current_color = colors[0] if colors else "red"

    pieces = {c: [{"type": "yard"} for _ in range(4)] for c in ("red", "blue", "green", "yellow")}
    return {
        "v": 1,
        "mode": mode,
        "turn_no": 1,
        "current_color": current_color,
        "dice": None,
        "dice_rolled_by": None,
        "players": players,
        "pieces": pieces,
        "winners": [],
        "finished_by_color": {c: False for c in ("red", "blue", "green", "yellow")},
    }


def _next_color(state: dict) -> Color:
    players = state.get("players") or []
    order = _color_order(players)
    if not order:
        return "red"
    cur = str(state.get("current_color") or order[0])
    if cur not in order:
        return order[0]
    idx = order.index(cur)
    return order[(idx + 1) % len(order)]


def _all_finished_for_color(state: dict, color: Color) -> bool:
    plist = (state.get("pieces") or {}).get(color) or []
    for p in plist:
        pos = _pos_from_state(p)
        if pos.type != "finished":
            return False
    return True


def _apply_move_to_state(state: dict, *, move: Move, actor_color: Color) -> dict:
    new_state = {**state}
    pieces = {**(state.get("pieces") or {})}
    new_state["pieces"] = pieces

    # Update actor piece.
    actor_list = list(pieces.get(actor_color) or [])
    actor_list = [dict(x) for x in actor_list]
    actor_list[int(move.piece_index)] = _pos_to_state(move.to_pos)
    pieces[actor_color] = actor_list

    # Apply captures: captured pieces sent back to yard.
    for c_color, c_idx in move.captures:
        clist = list(pieces.get(c_color) or [])
        clist = [dict(x) for x in clist]
        clist[int(c_idx)] = {"type": "yard"}
        pieces[c_color] = clist

    # Mark finished if all pieces finished.
    finished_by_color = dict(new_state.get("finished_by_color") or {})
    for color in ("red", "blue", "green", "yellow"):
        finished_by_color[color] = _all_finished_for_color(new_state, color)
    new_state["finished_by_color"] = finished_by_color

    return new_state


def _legal_move_for_piece(state: dict, *, color: Color, piece_index: int, dice: int) -> Optional[Move]:
    pieces = state.get("pieces") or {}
    piece = (pieces.get(color) or [])[piece_index]
    from_pos = _pos_from_state(piece)

    if from_pos.type == "finished":
        return None

    # Rule: enter board from yard only on 6
    if from_pos.type == "yard":
        if int(dice) != 6:
            return None
        start_idx = START_RING_INDEX[color]
        # If start square has own piece(s) already, entry is blocked in this simplified engine.
        occ = _piece_at_ring(state, ring_idx=start_idx)
        if any(oc_color == color for oc_color, _ in occ):
            return None
        captures: List[Tuple[Color, int]] = []
        if occ and not _is_safe_ring_square(start_idx):
            # capture all opponent pieces on that square
            captures = [(oc_color, oc_pi) for (oc_color, oc_pi) in occ if oc_color != color]
        return Move(piece_index=piece_index, from_pos=from_pos, to_pos=PiecePos(type="ring", index=start_idx), captures=captures)

    # Move along home stretch
    if from_pos.type == "home":
        cur = int(from_pos.index or 0)
        nxt = cur + int(dice)
        if nxt < 5:
            return Move(piece_index=piece_index, from_pos=from_pos, to_pos=PiecePos(type="home", index=nxt), captures=[])
        if nxt == 5:
            return Move(piece_index=piece_index, from_pos=from_pos, to_pos=PiecePos(type="finished", index=None), captures=[])
        return None  # cannot overshoot home

    # Move on ring
    if from_pos.type == "ring":
        cur_ring = int(from_pos.index or 0)
        entry_ring = HOME_ENTRY_RING_INDEX[color]

        # If crossing into home: only when passing the entry_ring exactly then continue into home.
        # We treat movement steps:
        # - If the path reaches the entry square and has remaining steps, move into home with remaining-1.
        steps = int(dice)
        ring_pos = cur_ring
        for step in range(1, steps + 1):
            ring_pos = _ring_advance(ring_pos, 1)
            if ring_pos == entry_ring:
                remaining = steps - step
                if remaining == 0:
                    # land exactly on entry ring square
                    break
                # enter home stretch at index=remaining-1
                home_idx = remaining - 1
                if home_idx > 5:
                    return None
                if home_idx == 5:
                    return Move(piece_index=piece_index, from_pos=from_pos, to_pos=PiecePos(type="finished", index=None), captures=[])
                return Move(piece_index=piece_index, from_pos=from_pos, to_pos=PiecePos(type="home", index=home_idx), captures=[])

        # Normal ring landing.
        dest_ring = ring_pos
        # Blocked by own piece(s)
        occ = _piece_at_ring(state, ring_idx=dest_ring)
        if any(oc_color == color for oc_color, _ in occ):
            return None

        captures: List[Tuple[Color, int]] = []
        if occ and not _is_safe_ring_square(dest_ring):
            captures = [(oc_color, oc_pi) for (oc_color, oc_pi) in occ if oc_color != color]

        return Move(piece_index=piece_index, from_pos=from_pos, to_pos=PiecePos(type="ring", index=dest_ring), captures=captures)

    return None


# PUBLIC_INTERFACE
def generate_legal_moves(*, state: dict, color: Color, dice: int) -> LegalMoves:
    """
    Generate legal moves for a given player color and dice.

    Args:
        state: canonical match state dict
        color: current player's color
        dice: dice value 1..6

    Returns:
        LegalMoves: list of legal Move entries.
    """
    _validate_state_shape(state)
    if int(dice) < 1 or int(dice) > 6:
        raise EngineValidationError("Dice must be between 1 and 6")

    moves: List[Move] = []
    for i in range(4):
        mv = _legal_move_for_piece(state, color=color, piece_index=i, dice=int(dice))
        if mv:
            moves.append(mv)
    return LegalMoves(dice=int(dice), moves=moves)


# PUBLIC_INTERFACE
def apply_turn(*, state: dict, color: Color, dice: int, piece_index: Optional[int]) -> Tuple[dict, dict]:
    """
    Apply a player's move decision and return updated state + a canonical turn payload.

    Args:
        state: current canonical state
        color: acting color (must equal state.current_color)
        dice: dice value 1..6
        piece_index: selected piece index 0..3, or None to "pass" if no legal moves

    Returns:
        (new_state, turn_payload)

    Raises:
        EngineValidationError: if move is illegal.
    """
    _validate_state_shape(state)
    if str(state.get("current_color")) != str(color):
        raise EngineValidationError("Not your turn (color mismatch)")

    legal = generate_legal_moves(state=state, color=color, dice=int(dice))

    if not legal.moves:
        # Only legal action is "pass"
        if piece_index is not None:
            raise EngineValidationError("No legal moves; must pass")
        new_state = {**state}
        new_state["dice"] = None
        new_state["dice_rolled_by"] = None
        # If no move, turn always passes to next player
        new_state["turn_no"] = int(state.get("turn_no") or 1) + 1
        new_state["current_color"] = _next_color(state)
        payload = {
            "type": "turn",
            "action": "pass",
            "color": color,
            "dice": int(dice),
            "move": None,
            "captures": [],
            "extra_turn": False,
        }
        return new_state, payload

    if piece_index is None:
        raise EngineValidationError("Move required (legal moves exist)")

    chosen = None
    for mv in legal.moves:
        if int(mv.piece_index) == int(piece_index):
            chosen = mv
            break
    if not chosen:
        raise EngineValidationError("Illegal move selection")

    new_state = _apply_move_to_state(state, move=chosen, actor_color=color)

    # Turn progression: extra turn on rolling a 6 (classic rule)
    extra_turn = int(dice) == 6
    new_state["dice"] = None
    new_state["dice_rolled_by"] = None

    if not extra_turn:
        new_state["turn_no"] = int(state.get("turn_no") or 1) + 1
        new_state["current_color"] = _next_color(state)
    else:
        # Same player continues; turn_no still increments to keep audit simple.
        new_state["turn_no"] = int(state.get("turn_no") or 1) + 1
        new_state["current_color"] = str(color)

    # Win condition: color finishes all pieces.
    if _all_finished_for_color(new_state, color):
        winners = list(new_state.get("winners") or [])
        if color not in winners:
            winners.append(color)
        new_state["winners"] = winners
        # For MVP: declare match complete when first winner exists.
        new_state["status"] = "finished"

    payload = {
        "type": "turn",
        "action": "move",
        "color": color,
        "dice": int(dice),
        "move": {
            "piece_index": int(chosen.piece_index),
            "from": {"type": chosen.from_pos.type, "index": chosen.from_pos.index},
            "to": {"type": chosen.to_pos.type, "index": chosen.to_pos.index},
        },
        "captures": [{"color": c, "piece_index": int(pi)} for c, pi in chosen.captures],
        "extra_turn": bool(extra_turn),
    }
    return new_state, payload
