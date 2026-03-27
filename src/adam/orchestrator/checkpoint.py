"""Checkpoint system — crash recovery via Redis.

Equivalent to Postwriter's CheckpointManager.
Tracks phase, current module/file, progress for resume after crash.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CheckpointData:
    """Checkpoint state for crash recovery."""
    project_id: str
    phase: str = "implementing"
    current_module_ordinal: int = 0
    current_file_ordinal: int = 0
    files_processed: int = 0
    total_files: int = 0
    total_repair_rounds: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)


class CheckpointManager:
    """Manages checkpoints in Redis for crash recovery."""

    KEY_PREFIX = "adam:checkpoint:"

    def __init__(self, redis_url: str = "redis://localhost:6379/1") -> None:
        self._redis_url = redis_url
        self._client: object | None = None

    async def _get_client(self) -> object:
        if self._client is None:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(self._redis_url)
        return self._client

    async def save(self, checkpoint: CheckpointData) -> None:
        """Save a checkpoint."""
        client = await self._get_client()
        key = f"{self.KEY_PREFIX}{checkpoint.project_id}"
        data = json.dumps(asdict(checkpoint))
        await client.set(key, data)  # type: ignore[union-attr]
        logger.debug("Checkpoint saved for %s", checkpoint.project_id)

    async def load(self, project_id: str) -> CheckpointData | None:
        """Load a checkpoint."""
        client = await self._get_client()
        key = f"{self.KEY_PREFIX}{project_id}"
        data = await client.get(key)  # type: ignore[union-attr]
        if data is None:
            return None
        try:
            parsed = json.loads(data)
            return CheckpointData(**{
                k: v for k, v in parsed.items()
                if k in CheckpointData.__dataclass_fields__
            })
        except (json.JSONDecodeError, TypeError):
            return None

    async def delete(self, project_id: str) -> None:
        """Delete a checkpoint."""
        client = await self._get_client()
        key = f"{self.KEY_PREFIX}{project_id}"
        await client.delete(key)  # type: ignore[union-attr]

    async def list_incomplete(self) -> list[CheckpointData]:
        """List all incomplete checkpoints."""
        client = await self._get_client()
        keys = []
        async for key in client.scan_iter(  # type: ignore[union-attr]
            match=f"{self.KEY_PREFIX}*"
        ):
            keys.append(key)

        checkpoints = []
        for key in keys:
            data = await client.get(key)  # type: ignore[union-attr]
            if data:
                try:
                    parsed = json.loads(data)
                    cp = CheckpointData(**{
                        k: v for k, v in parsed.items()
                        if k in CheckpointData.__dataclass_fields__
                    })
                    if cp.phase != "complete":
                        checkpoints.append(cp)
                except (json.JSONDecodeError, TypeError):
                    continue
        return checkpoints

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client is not None:
            await self._client.aclose()  # type: ignore[union-attr]
            self._client = None
