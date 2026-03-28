"""ProjectStore — CRUD for all canonical entities with event logging.

Equivalent to Postwriter's CanonStore. All mutations are recorded via EventLogger.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from adam.models.analytics import (
    RepairActionRecord,
    ScoreVectorRecord,
    ValidationResultRecord,
)
from adam.models.core import File, FileDependency, Module, Project
from adam.models.obligations import Obligation
from adam.models.testing import Test
from adam.store.events import EventLogger


class ProjectStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events = EventLogger(session)

    # -------------------------------------------------------------------
    # Project
    # -------------------------------------------------------------------

    async def create_project(self, **kwargs: Any) -> Project:
        project = Project(**kwargs)
        self._session.add(project)
        await self._session.flush()
        await self._events.record(
            project.id, "project", project.id, "created", kwargs
        )
        return project

    async def get_project(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_project_full(self, project_id: uuid.UUID) -> Project | None:
        stmt = (
            select(Project)
            .where(Project.id == project_id)
            .options(
                selectinload(Project.modules).selectinload(Module.files),
                selectinload(Project.obligations),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_project(self, project_id: uuid.UUID, **kwargs: Any) -> Project | None:
        project = await self.get_project(project_id)
        if project is None:
            return None
        for k, v in kwargs.items():
            setattr(project, k, v)
        await self._session.flush()
        await self._events.record(
            project_id, "project", project_id, "updated", kwargs
        )
        return project

    # -------------------------------------------------------------------
    # Modules
    # -------------------------------------------------------------------

    async def create_module(self, project_id: uuid.UUID, **kwargs: Any) -> Module:
        module = Module(project_id=project_id, **kwargs)
        self._session.add(module)
        await self._session.flush()
        await self._events.record(
            project_id, "module", module.id, "created", kwargs
        )
        return module

    async def get_modules(self, project_id: uuid.UUID) -> list[Module]:
        stmt = (
            select(Module)
            .where(Module.project_id == project_id)
            .order_by(Module.ordinal)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_module(self, module_id: uuid.UUID) -> Module | None:
        return await self._session.get(Module, module_id)

    async def get_module_with_files(self, module_id: uuid.UUID) -> Module | None:
        stmt = (
            select(Module)
            .where(Module.id == module_id)
            .options(selectinload(Module.files))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_module(
        self, project_id: uuid.UUID, module_id: uuid.UUID, **kwargs: Any
    ) -> Module | None:
        module = await self.get_module(module_id)
        if module is None:
            return None
        for k, v in kwargs.items():
            setattr(module, k, v)
        await self._session.flush()
        await self._events.record(
            project_id, "module", module_id, "updated", kwargs
        )
        return module

    # -------------------------------------------------------------------
    # Files
    # -------------------------------------------------------------------

    async def create_file(self, project_id: uuid.UUID, **kwargs: Any) -> File:
        f = File(**kwargs)
        self._session.add(f)
        await self._session.flush()
        await self._events.record(
            project_id, "file", f.id, "created", {"path": f.path, **kwargs}
        )
        return f

    async def get_files(self, module_id: uuid.UUID) -> list[File]:
        stmt = (
            select(File)
            .where(File.module_id == module_id)
            .order_by(File.ordinal)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_file(self, file_id: uuid.UUID) -> File | None:
        return await self._session.get(File, file_id)

    async def get_file_by_path(self, module_id: uuid.UUID, path: str) -> File | None:
        stmt = select(File).where(File.module_id == module_id, File.path == path)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_file_by_path(self, project_id: uuid.UUID, path: str) -> File | None:
        """Find a file by path across all modules in a project."""
        stmt = (
            select(File)
            .join(Module, File.module_id == Module.id)
            .where(Module.project_id == project_id, File.path == path)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_file(
        self, project_id: uuid.UUID, file_id: uuid.UUID, **kwargs: Any
    ) -> File | None:
        f = await self.get_file(file_id)
        if f is None:
            return None
        for k, v in kwargs.items():
            setattr(f, k, v)
        await self._session.flush()
        await self._events.record(
            project_id, "file", file_id, "updated", kwargs
        )
        return f

    # -------------------------------------------------------------------
    # File dependencies
    # -------------------------------------------------------------------

    async def add_file_dependency(
        self,
        project_id: uuid.UUID,
        source_file_id: uuid.UUID,
        target_file_id: uuid.UUID,
        dependency_type: str,
        description: str = "",
    ) -> FileDependency:
        dep = FileDependency(
            source_file_id=source_file_id,
            target_file_id=target_file_id,
            dependency_type=dependency_type,
            description=description,
        )
        self._session.add(dep)
        await self._session.flush()
        await self._events.record(
            project_id, "file_dependency", dep.id, "created",
            {"source": str(source_file_id), "target": str(target_file_id)},
        )
        return dep

    # -------------------------------------------------------------------
    # Tests
    # -------------------------------------------------------------------

    async def create_test(self, project_id: uuid.UUID, **kwargs: Any) -> Test:
        t = Test(project_id=project_id, **kwargs)
        self._session.add(t)
        await self._session.flush()
        await self._events.record(
            project_id, "test", t.id, "created", {"path": t.path}
        )
        return t

    async def get_tests(self, project_id: uuid.UUID) -> list[Test]:
        stmt = select(Test).where(Test.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_tests_for_file(self, file_id: uuid.UUID) -> list[Test]:
        stmt = select(Test).where(Test.file_id == file_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_test(
        self, project_id: uuid.UUID, test_id: uuid.UUID, **kwargs: Any
    ) -> Test | None:
        t = await self._session.get(Test, test_id)
        if t is None:
            return None
        for k, v in kwargs.items():
            setattr(t, k, v)
        await self._session.flush()
        await self._events.record(
            project_id, "test", test_id, "updated", kwargs
        )
        return t

    # -------------------------------------------------------------------
    # Obligations
    # -------------------------------------------------------------------

    async def create_obligation(self, project_id: uuid.UUID, **kwargs: Any) -> Obligation:
        ob = Obligation(project_id=project_id, **kwargs)
        self._session.add(ob)
        await self._session.flush()
        await self._events.record(
            project_id, "obligation", ob.id, "created", kwargs
        )
        return ob

    async def get_obligations(
        self, project_id: uuid.UUID, status: str | None = None
    ) -> list[Obligation]:
        stmt = select(Obligation).where(Obligation.project_id == project_id)
        if status:
            stmt = stmt.where(Obligation.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_obligation(
        self, project_id: uuid.UUID, obligation_id: uuid.UUID, **kwargs: Any
    ) -> Obligation | None:
        ob = await self._session.get(Obligation, obligation_id)
        if ob is None:
            return None
        for k, v in kwargs.items():
            setattr(ob, k, v)
        await self._session.flush()
        await self._events.record(
            project_id, "obligation", obligation_id, "updated", kwargs
        )
        return ob

    # -------------------------------------------------------------------
    # Validation results
    # -------------------------------------------------------------------

    async def record_validation(self, **kwargs: Any) -> ValidationResultRecord:
        rec = ValidationResultRecord(**kwargs)
        self._session.add(rec)
        await self._session.flush()
        return rec

    # -------------------------------------------------------------------
    # Repair actions
    # -------------------------------------------------------------------

    async def record_repair_action(self, **kwargs: Any) -> RepairActionRecord:
        rec = RepairActionRecord(**kwargs)
        self._session.add(rec)
        await self._session.flush()
        return rec

    # -------------------------------------------------------------------
    # Score vectors
    # -------------------------------------------------------------------

    async def record_score_vector(self, **kwargs: Any) -> ScoreVectorRecord:
        rec = ScoreVectorRecord(**kwargs)
        self._session.add(rec)
        await self._session.flush()
        return rec

    # -------------------------------------------------------------------
    # Commit
    # -------------------------------------------------------------------

    async def commit(self) -> None:
        await self._session.commit()
