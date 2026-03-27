"""Append-only event logger for traceability."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from adam.models.events import Event


def _sanitize_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert non-JSON-serializable values (UUIDs) to strings."""
    if payload is None:
        return None
    clean: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, uuid.UUID):
            clean[k] = str(v)
        elif isinstance(v, dict):
            clean[k] = _sanitize_payload(v)  # type: ignore[assignment]
        elif isinstance(v, list):
            clean[k] = [str(i) if isinstance(i, uuid.UUID) else i for i in v]
        else:
            clean[k] = v
    return clean


class EventLogger:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        project_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        # Get next sequence number for this project
        stmt = select(func.coalesce(func.max(Event.sequence), 0)).where(
            Event.project_id == project_id
        )
        result = await self._session.execute(stmt)
        next_seq = result.scalar_one() + 1

        event = Event(
            project_id=project_id,
            sequence=next_seq,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            payload=_sanitize_payload(payload),
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_events(
        self,
        project_id: uuid.UUID,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[Event]:
        stmt = select(Event).where(Event.project_id == project_id)
        if entity_type:
            stmt = stmt.where(Event.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(Event.entity_id == entity_id)
        stmt = stmt.order_by(Event.sequence.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
