"""Git-based snapshot and revert for the refinement loop.

Before each fix attempt, we record the current commit hash.
If the fix makes things worse, we revert to that snapshot.
If it's better or neutral, we commit the change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from adam.git.manager import GitManager

logger = logging.getLogger(__name__)


@dataclass
class Snapshot:
    """A recorded point in git history we can revert to."""
    commit_hash: str
    description: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.commit_hash)


class SnapshotManager:
    """Takes and restores git snapshots for safe refinement."""

    def __init__(self, git: GitManager) -> None:
        self._git = git

    async def take(self, description: str = "") -> Snapshot:
        """Record the current state as a snapshot.

        If there are uncommitted changes, stash them first and commit
        a checkpoint so we have a clean hash to revert to.
        """
        # Ensure working tree is clean
        if not await self._git.is_clean():
            # Commit any outstanding work so we have a clean baseline
            await self._git.add()
            result = await self._git.commit(
                f"[adam:refine] checkpoint before fix — {description}",
            )
            if not result.success:
                logger.warning(
                    "Could not commit checkpoint: %s", result.error,
                )

        commit_hash = await self._git.current_hash()
        logger.info("Snapshot taken: %s (%s)", commit_hash, description)
        return Snapshot(commit_hash=commit_hash, description=description)

    async def revert(self, snapshot: Snapshot) -> bool:
        """Revert the working tree to a snapshot.

        Uses git reset --hard to the snapshot commit, discarding
        everything after it. Only safe because the refinement loop
        operates on one fix at a time.
        """
        if not snapshot.valid:
            logger.error("Cannot revert to invalid snapshot")
            return False

        logger.info(
            "Reverting to snapshot %s (%s)",
            snapshot.commit_hash, snapshot.description,
        )

        result = await self._git._run(
            f"git reset --hard {snapshot.commit_hash}"
        )
        if result.success:
            logger.info("Reverted successfully")
            return True

        logger.error("Revert failed: %s", result.output)
        return False

    async def commit_fix(
        self,
        message: str,
        paths: list[str] | None = None,
    ) -> str:
        """Commit a successful fix. Returns commit hash."""
        if paths:
            await self._git.add(*paths)
        else:
            await self._git.add()

        result = await self._git.commit(
            f"[adam:refine] {message}",
        )

        if result.success:
            logger.info("Fix committed: %s (%s)", result.commit_hash, message)
            return result.commit_hash

        logger.warning("Commit failed: %s", result.error)
        return ""
