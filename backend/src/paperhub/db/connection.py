import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

# Process-wide async lock that bounds SQLite write concurrency at the
# application layer. Every write transaction acquired through
# ``write_transaction()`` queues on this lock — so the SQLite file lock
# never sees more than one concurrent writer from this process, regardless
# of how many connections are open (one per HTTP request, the marker
# worker, MCP servers, etc.). This is the load-bearing safety net behind
# the v2.23.2 concurrent-ingest hotfix: bumping ``busy_timeout`` alone was
# insufficient because each ingest sprayed ~400 INSERTs at the lock and
# contention could stack faster than the timeout cleared it.
_DB_WRITE_LOCK = asyncio.Lock()


async def configure_connection(conn: aiosqlite.Connection) -> None:
    """Apply the standard PRAGMAs every PaperHub connection needs.

    WAL lets concurrent readers run alongside a single writer with no
    reader/writer blocking, and busy_timeout makes a writer wait for a
    lock instead of raising ``database is locked`` immediately. The 30 s
    timeout (v2.23.2 hotfix; was 5 s) is defense-in-depth — the primary
    protection is the process-wide ``_DB_WRITE_LOCK`` acquired by
    ``write_transaction()``; the timeout covers any straggling caller
    that hasn't been migrated to the managed path yet.
    """
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA busy_timeout = 30000")


@asynccontextmanager
async def open_db(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(db_path) as conn:
        await configure_connection(conn)
        yield conn


@asynccontextmanager
async def write_transaction(
    conn: aiosqlite.Connection,
) -> AsyncIterator[None]:
    """Run a write transaction with process-wide serialisation.

    Acquires ``_DB_WRITE_LOCK`` (only one transaction managed by this
    context manager is open at a time, app-wide), issues
    ``BEGIN IMMEDIATE`` so the SQLite write lock is acquired up-front
    instead of on the first INSERT, commits on success, ROLLBACKs on any
    exception, and always releases both locks. Use it anywhere a write
    transaction touches more than one statement OR contends with other
    writers — i.e. essentially every place that previously wrote a
    sequence of statements and an explicit ``commit()``.

    Example::

        async with write_transaction(conn):
            await conn.execute("INSERT ...")
            await conn.execute("INSERT ...")
        # auto-commit; on exception, auto-rollback + re-raise

    BEGIN IMMEDIATE is the right BEGIN flavour here: with deferred BEGIN
    (the SQLite default), the write lock is acquired lazily on the first
    INSERT, which gave us the same ``database is locked`` failure mode as
    no transaction at all when two writers raced. IMMEDIATE puts the wait
    at BEGIN time where ``busy_timeout`` (and our asyncio lock) can apply
    cleanly.
    """
    async with _DB_WRITE_LOCK:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            await conn.commit()
        except BaseException:
            # Best-effort ROLLBACK — if the connection itself is gone or
            # the rollback fails, the original exception is still what
            # the caller needs to see, so we swallow rollback errors.
            with contextlib.suppress(Exception):
                await conn.execute("ROLLBACK")
            raise
