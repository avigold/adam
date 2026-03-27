"""Obligation lifecycle tracking — tracks what was specified vs built vs tested.

Links obligations to implementing files and tests. Updates status as
the implementation loop progresses. Used by stop conditions to determine
project completeness.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from adam.store.store import ProjectStore

logger = logging.getLogger(__name__)


class ObligationTracker:
    """Tracks obligation lifecycle through implementation phases."""

    def __init__(self, session: AsyncSession) -> None:
        self._store = ProjectStore(session)

    async def link_file_to_obligations(
        self,
        project_id: uuid.UUID,
        file_path: str,
        file_purpose: str,
    ) -> int:
        """Link a completed file to matching obligations.

        First checks explicit links (set during planning). Falls back to
        keyword matching for obligations that weren't explicitly linked.
        """
        obligations = await self._store.get_obligations(project_id, status="open")
        linked = 0

        for ob in obligations:
            # Explicit link: file was already assigned during planning
            already_linked = file_path in (ob.implementing_files or [])
            if already_linked:
                await self._store.update_obligation(
                    project_id,
                    ob.id,
                    status="implemented",
                )
                linked += 1
                continue

            # Fallback: keyword matching for unlinked obligations
            if _purposes_match(file_purpose, ob.description):
                impl_files = list(ob.implementing_files or [])
                if file_path not in impl_files:
                    impl_files.append(file_path)
                    await self._store.update_obligation(
                        project_id,
                        ob.id,
                        implementing_files=impl_files,
                        status="implemented",
                    )
                    linked += 1
                    logger.debug(
                        "Linked %s to obligation: %s",
                        file_path, ob.description[:60],
                    )

        return linked

    async def mark_tested(
        self,
        project_id: uuid.UUID,
        file_path: str,
        test_path: str,
    ) -> int:
        """Mark obligations as tested when their tests pass."""
        obligations = await self._store.get_obligations(
            project_id, status="implemented"
        )
        updated = 0

        for ob in obligations:
            if file_path in (ob.implementing_files or []):
                test_files = list(ob.testing_files or [])
                if test_path not in test_files:
                    test_files.append(test_path)
                    await self._store.update_obligation(
                        project_id,
                        ob.id,
                        testing_files=test_files,
                        status="tested",
                    )
                    updated += 1

        return updated

    async def mark_verified(
        self,
        project_id: uuid.UUID,
        obligation_id: uuid.UUID,
    ) -> None:
        """Mark an obligation as fully verified (tests + critics pass)."""
        await self._store.update_obligation(
            project_id, obligation_id, status="verified"
        )

    async def get_completion_status(
        self,
        project_id: uuid.UUID,
    ) -> ObligationStatus:
        """Get overall obligation completion status."""
        all_obs = await self._store.get_obligations(project_id)
        if not all_obs:
            return ObligationStatus(
                total=0, open=0, implemented=0,
                tested=0, verified=0, blocked=0,
                complete=True,
            )

        counts: dict[str, int] = {}
        for ob in all_obs:
            counts[ob.status] = counts.get(ob.status, 0) + 1

        total = len(all_obs)
        open_count = counts.get("open", 0)
        blocked = counts.get("blocked", 0)
        implemented = counts.get("implemented", 0)
        tested = counts.get("tested", 0)
        verified = counts.get("verified", 0)

        return ObligationStatus(
            total=total,
            open=open_count,
            implemented=implemented,
            tested=tested,
            verified=verified,
            blocked=blocked,
            complete=(open_count == 0 and blocked == 0),
        )


class ObligationStatus:
    """Summary of obligation completion."""

    def __init__(
        self,
        total: int,
        open: int,
        implemented: int,
        tested: int,
        verified: int,
        blocked: int,
        complete: bool,
    ) -> None:
        self.total = total
        self.open = open
        self.implemented = implemented
        self.tested = tested
        self.verified = verified
        self.blocked = blocked
        self.complete = complete

    @property
    def completion_ratio(self) -> float:
        if self.total == 0:
            return 1.0
        done = self.implemented + self.tested + self.verified
        return done / self.total


def _purposes_match(file_purpose: str, obligation_desc: str) -> bool:
    """Simple keyword overlap check between file purpose and obligation."""
    fp_words = set(file_purpose.lower().split())
    ob_words = set(obligation_desc.lower().split())
    # Remove common stop words
    stop = {"the", "a", "an", "is", "are", "and", "or", "to", "for", "of", "in", "on", "with"}
    fp_words -= stop
    ob_words -= stop
    if not fp_words or not ob_words:
        return False
    overlap = fp_words & ob_words
    # Match if at least 30% of the smaller set overlaps
    min_size = min(len(fp_words), len(ob_words))
    return len(overlap) / min_size >= 0.3
