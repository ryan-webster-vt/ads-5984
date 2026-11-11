"""SQLite connection factory, migration runner, and transaction helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from upset_profiler.errors import StorageError

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True)
class Migration:
    """A versioned SQLite migration."""

    version: int
    name: str
    sql: str


@lru_cache(maxsize=1)
def load_migrations() -> tuple[Migration, ...]:
    """Return the ordered migrations shipped with the package."""
    return (Migration(version=1, name="initial_schema", sql=SCHEMA_PATH.read_text("utf-8")),)


def connect(
    database: str | Path,
    *,
    create_parent: bool = True,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    """Open a SQLite connection with safe defaults.

    ``database`` may be a filesystem path or ``":memory:"`` for tests.
    """
    if str(database) == ":memory:":
        connection = sqlite3.connect(":memory:", timeout=timeout, isolation_level=None)
    else:
        path = Path(database)
        if create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=timeout, isolation_level=None)

    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        if str(database) != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        connection.close()
        raise StorageError("failed to configure sqlite connection") from exc
    return connection


def applied_versions(connection: sqlite3.Connection) -> tuple[int, ...]:
    """Return migration versions already applied to the database."""
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        rows = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise StorageError("failed to read schema migrations") from exc
    return tuple(int(row["version"]) for row in rows)


def migrate(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] | None = None,
) -> list[int]:
    """Apply pending migrations and return the newly applied versions."""
    from upset_profiler.context import utc_now

    pending = migrations if migrations is not None else load_migrations()
    already_applied = set(applied_versions(connection))
    newly_applied: list[int] = []

    for migration in sorted(pending, key=lambda item: item.version):
        if migration.version in already_applied:
            continue
        try:
            connection.executescript(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, utc_now().isoformat()),
            )
        except sqlite3.Error as exc:
            raise StorageError(
                "failed to apply migration",
                context={"version": migration.version, "name": migration.name},
            ) from exc
        newly_applied.append(migration.version)

    return newly_applied


def initialize_database(
    database: str | Path,
    *,
    migrations: Sequence[Migration] | None = None,
) -> sqlite3.Connection:
    """Open a database, apply pending migrations, and return the connection."""
    connection = connect(database)
    try:
        migrate(connection, migrations)
    except StorageError:
        connection.close()
        raise
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside an explicit SQLite transaction."""
    try:
        connection.execute("BEGIN")
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
