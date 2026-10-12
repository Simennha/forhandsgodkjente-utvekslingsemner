"""Notes and project status stores (memory + PostgreSQL)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class Note:
    id: uuid.UUID
    created_at: datetime
    text: str
    tags: tuple[str, ...]
    project: str | None


@dataclass(frozen=True)
class Project:
    name: str
    status: str
    updated_at: datetime
    history: tuple[dict[str, Any], ...] = ()


class NotesStore(Protocol):
    def add(self, text: str, tags: list[str], project: str | None, now: datetime) -> Note: ...
    def search(self, query: str | None, limit: int) -> list[Note]: ...
    def since(self, since: datetime) -> list[Note]: ...


class ProjectStore(Protocol):
    def upsert(self, name: str, status: str, now: datetime) -> Project: ...
    def get(self, name: str) -> Project | None: ...
    def all(self) -> list[Project]: ...


@dataclass
class MemoryNotes:
    notes: list[Note] = field(default_factory=list)

    def add(self, text: str, tags: list[str], project: str | None, now: datetime) -> Note:
        note = Note(uuid.uuid4(), now, text, tuple(tags), project)
        self.notes.append(note)
        return note

    def search(self, query: str | None, limit: int) -> list[Note]:
        q = (query or "").lower()
        hits = [n for n in reversed(self.notes) if q in n.text.lower() or q in n.tags]
        return hits[:limit]

    def since(self, since: datetime) -> list[Note]:
        return [n for n in self.notes if n.created_at >= since]


@dataclass
class MemoryProjects:
    projects: dict[str, Project] = field(default_factory=dict)

    def upsert(self, name: str, status: str, now: datetime) -> Project:
        old = self.projects.get(name)
        history = (
            (*old.history, {"status": old.status, "at": old.updated_at.isoformat()}) if old else ()
        )
        project = Project(name, status, now, history[-20:])
        self.projects[name] = project
        return project

    def get(self, name: str) -> Project | None:
        return self.projects.get(name)

    def all(self) -> list[Project]:
        return sorted(self.projects.values(), key=lambda p: p.name)


class PgNotes:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def add(self, text: str, tags: list[str], project: str | None, now: datetime) -> Note:
        note = Note(uuid.uuid4(), now, text, tuple(tags), project)
        with self._pool.connection() as conn:
            conn.execute(
                "insert into core.notes (id, created_at, text, tags, project) "
                "values (%s,%s,%s,%s,%s)",
                (note.id, now, text, list(tags), project),
            )
        return note

    def search(self, query: str | None, limit: int) -> list[Note]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select id, created_at, text, tags, project from core.notes "
                "where %s::text is null or text ilike '%%' || %s || '%%' or %s = any(tags) "
                "order by created_at desc limit %s",
                (query, query, query, limit),
            ).fetchall()
        return [Note(r[0], r[1], r[2], tuple(r[3]), r[4]) for r in rows]

    def since(self, since: datetime) -> list[Note]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select id, created_at, text, tags, project from core.notes where created_at >= %s "
                "order by created_at",
                (since,),
            ).fetchall()
        return [Note(r[0], r[1], r[2], tuple(r[3]), r[4]) for r in rows]


class PgProjects:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert(self, name: str, status: str, now: datetime) -> Project:
        with self._pool.connection() as conn:
            row = conn.execute(
                "insert into core.projects (name, status, updated_at) values (%s,%s,%s) "
                "on conflict (name) do update set "
                "history = (core.projects.history || jsonb_build_array(jsonb_build_object("
                "'status', core.projects.status, 'at', core.projects.updated_at)))"
                ", status = excluded.status, updated_at = excluded.updated_at "
                "returning name, status, updated_at, history",
                (name, status, now),
            ).fetchone()
        if row is None:
            raise RuntimeError("project upsert returned no row")
        return Project(row[0], row[1], row[2], tuple(row[3]))

    def get(self, name: str) -> Project | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select name, status, updated_at, history from core.projects where name = %s",
                (name,),
            ).fetchone()
        return Project(row[0], row[1], row[2], tuple(row[3])) if row else None

    def all(self) -> list[Project]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select name, status, updated_at, history from core.projects order by name"
            ).fetchall()
        return [Project(r[0], r[1], r[2], tuple(r[3])) for r in rows]
