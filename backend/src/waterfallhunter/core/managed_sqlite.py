from __future__ import annotations

import sqlite3
from pathlib import Path


class ManagedSQLiteError(RuntimeError):
    """Raised before mutation when managed SQLite invariants are unavailable."""


class ManagedSQLiteConnection(sqlite3.Connection):
    """SQLite connection whose context manager also closes the connection."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect_managed_sqlite(
    database: str | Path,
    *,
    timeout: float = 5.0,
    isolation_level: str | None = "DEFERRED",
    uri: bool = False,
) -> sqlite3.Connection:
    """Open a managed connection with verified SQLite invariants.

    ``timeout=`` configures Python's initial connection/open behaviour, but it
    does not make the SQLite connection's own busy handler explicit. Under the
    hunter's concurrent writes a raw/default busy handler can fail immediately
    with ``database is locked`` while a transaction is about to finish. Apply
    the same bound as the connection timeout to every managed connection.

    WAL is intentionally *not* set here: journal mode is a file-level
    migration invariant, and negotiating it on every read/write connection can
    itself require an exclusive lock.
    """

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            str(database),
            timeout=timeout,
            isolation_level=isolation_level,
            uri=uri,
            factory=ManagedSQLiteConnection,
        )
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        if row != (1,):
            raise ManagedSQLiteError("MANAGED_SQLITE_FOREIGN_KEYS_UNAVAILABLE")
        busy_timeout_ms = max(0, int(timeout * 1000))
        try:
            conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            busy_row = conn.execute("PRAGMA busy_timeout").fetchone()
            if busy_row != (busy_timeout_ms,):
                raise ManagedSQLiteError("MANAGED_SQLITE_BUSY_TIMEOUT_UNAVAILABLE")
        except Exception as exc:
            if isinstance(exc, ManagedSQLiteError):
                raise
            raise ManagedSQLiteError("MANAGED_SQLITE_BUSY_TIMEOUT_UNAVAILABLE") from exc
        return conn
    except Exception as exc:
        if conn is not None:
            conn.close()
        if isinstance(exc, ManagedSQLiteError):
            raise
        raise ManagedSQLiteError("MANAGED_SQLITE_FOREIGN_KEYS_UNAVAILABLE") from exc


class _ManagedConnection:
    """Connection wrapper whose context manager commits, rolls back, and closes."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def managed_connection(database: str | Path, *, timeout: float = 5.0) -> _ManagedConnection:
    """Open, verify, and hand out a self-closing managed SQLite connection."""
    return _ManagedConnection(connect_managed_sqlite(database, timeout=timeout))
