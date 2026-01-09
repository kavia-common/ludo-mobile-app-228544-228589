from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TurnPublishedEvent:
    """Event payload for a committed turn update (stub)."""

    topic: str
    match_id: str
    turn_no: int
    payload: Dict[str, Any]


class EventPublisher:
    """
    Internal event publisher stub.

    In a later step, this can be backed by NATS/Kafka, but for now it just logs.
    """

    def __init__(self, *, topic_turn_updates: str = "TODO.ludo.turns") -> None:
        self._topic_turn_updates = topic_turn_updates

    # PUBLIC_INTERFACE
    async def publish_turn_update(self, *, match_id: str, turn_no: int, payload: Dict[str, Any]) -> None:
        """
        Publish a turn update event (stub).

        This should be called after a turn is committed to DB and cache.

        Args:
            match_id: match id
            turn_no: turn number
            payload: canonical event payload
        """
        evt = TurnPublishedEvent(topic=self._topic_turn_updates, match_id=match_id, turn_no=int(turn_no), payload=payload)
        # Stub behavior: log only
        logger.info("Event publish stub: %s", evt)
        return


# PUBLIC_INTERFACE
def get_event_publisher() -> EventPublisher:
    """Get the event publisher stub instance (stateless)."""
    return EventPublisher()
