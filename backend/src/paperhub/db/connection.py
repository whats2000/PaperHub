import asyncio
import contextlib
import contextvars
import sqlite3
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

# The task currently inside ``write_transaction``. The lock is not
# re-entrant, so a helper that opens its own transaction checks
# ``in_write_transaction`` and fails fast instead of deadlocking when called
# from inside one. The task (not a bool) is stored because a child task
# copies the context: it must not inherit "inside" — it runs on its own and
# may legitimately open a transaction once the parent's commits.
_IN_WRITE_TX: contextvars.ContextVar[asyncio.Task[object] | None] = (
    contextvars.ContextVar("paperhub_in_write_tx", default=None)
)


def in_write_transaction() -> bool:
    """Whether the current task is inside a ``write_transaction`` block."""
    owner = _IN_WRITE_TX.get()
    return owner is not None and owner is asyncio.current_task()


# The task currently holding ``_DB_WRITE_LOCK`` (via ``write_transaction``
# or ``write_lock``). Stored as the task, like ``_IN_WRITE_TX``, so child
# tasks do not inherit ownership.
_LOCK_OWNER: contextvars.ContextVar[asyncio.Task[object] | None] = (
    contextvars.ContextVar("paperhub_write_lock_owner", default=None)
)


def holds_write_lock() -> bool:
    """Whether the current task holds ``_DB_WRITE_LOCK``."""
    owner = _LOCK_OWNER.get()
    return owner is not None and owner is asyncio.current_task()


@asynccontextmanager
async def write_lock(
    conn: aiosqlite.Connection | None = None,
) -> AsyncIterator[None]:
    """Hold ``_DB_WRITE_LOCK`` around a raw ``execute`` + ``commit`` write.

    Re-entrant per task: a task that already holds the lock (inside
    ``write_transaction`` or an outer ``write_lock``) proceeds without
    waiting. Tasks sharing one connection (an agent step's concurrent tool
    calls) must take it for every write, or one task's statements land
    inside — and its ``commit`` ends — another task's open transaction.

    With ``conn``, a block that raises is rolled back before the lock is
    released, so a half-written implicit transaction never stays open for
    the next writer's commit. A re-entered block leaves that to the owner.
    """
    if holds_write_lock():
        yield
        return
    async with _DB_WRITE_LOCK:
        token = _LOCK_OWNER.set(asyncio.current_task())
        try:
            yield
        except BaseException:
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.rollback()
            raise
        finally:
            with contextlib.suppress(ValueError):  # closed from another context
                _LOCK_OWNER.reset(token)


def norm_for_match(s: str | None) -> str:
    """Normalize a string for forgiving substring search.

    Lowercase (Unicode-aware via ``str.lower``) and drop every
    non-alphanumeric character. So ``X-VLA: Soft-Prompted`` →
    ``xvlasoftprompted`` — a user typing ``xvla`` now matches the
    hyphen-and-colon-broken title via substring. The Python UDF is
    registered into every connection by :func:`configure_connection`
    so SQL can call it as ``paperhub_norm(col)``. Used by the
    /papers/library Add-from-Library picker; the LLM-driven
    ``search_library_dispatch`` path keeps its own FTS5 query.
    """
    if not s:
        return ""
    return "".join(c for c in s.lower() if c.isalnum())


async def configure_connection(conn: aiosqlite.Connection) -> None:
    """Apply the standard PRAGMAs every PaperHub connection needs.

    WAL lets concurrent readers run alongside a single writer with no
    reader/writer blocking, and busy_timeout makes a writer wait for a
    lock instead of raising ``database is locked`` immediately. The 30 s
    timeout (v2.23.2 hotfix; was 5 s) is defense-in-depth — the primary
    protection is the process-wide ``_DB_WRITE_LOCK`` acquired by
    ``write_transaction()``; the timeout covers any straggling caller
    that hasn't been migrated to the managed path yet.

    Also registers the ``paperhub_norm`` UDF used by the Add-from-Library
    substring search (see :func:`norm_for_match`). Registration is
    per-connection because aiosqlite opens one connection per call site —
    the cost is a single function-pointer bind, microseconds.
    """
    await conn.execute("PRAGMA foreign_keys = ON")
    # busy_timeout first. The WAL switch on a not-yet-WAL DB can still raise
    # SQLITE_BUSY without consulting the busy handler, so retry it briefly.
    await conn.execute("PRAGMA busy_timeout = 30000")
    for attempt in range(50):
        try:
            await conn.execute("PRAGMA journal_mode = WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 49:
                raise
            await asyncio.sleep(0.05)
    await conn.create_function("paperhub_norm", 1, norm_for_match)


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
        owner_token = _LOCK_OWNER.set(asyncio.current_task())
        try:
            await conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            _LOCK_OWNER.reset(owner_token)
            raise
        token = _IN_WRITE_TX.set(asyncio.current_task())
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
        finally:
            with contextlib.suppress(ValueError):  # closed from another context
                _IN_WRITE_TX.reset(token)
            with contextlib.suppress(ValueError):
                _LOCK_OWNER.reset(owner_token)
