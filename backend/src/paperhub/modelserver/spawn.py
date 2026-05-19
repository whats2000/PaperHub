"""Spawn the model server as a sibling process of the backend.

Called from ``app._lifespan``. The flow is:

  1. TCP-probe ``host:port``. If already reachable, log and return None
     (operator spun it up manually, or a previous backend run's
     subprocess outlived its parent).
  2. Otherwise spawn ``python -m paperhub.modelserver`` with the
     parent's environment inherited (so ``PAPERHUB_MODEL_SERVER_PORT``
     etc. propagate). Don't pass cwd — relies on the same uv-managed
     interpreter the backend is running under.
  3. Wait until ``/health`` answers 200, up to ``ready_timeout_s``. If
     the server doesn't come up, terminate the subprocess and return
     None — the embedder/reranker HTTP clients will surface the
     connection error to the caller. Lifespan is non-fatal on this
     path; in-process models can be forced via
     ``PAPERHUB_INPROCESS_MODELS=1`` if the operator can't run two
     processes.
  4. On backend shutdown, ``terminate_subprocess`` SIGTERMs the
     subprocess, falling back to SIGKILL after a short grace.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
import time

import httpx

_LOG = logging.getLogger(__name__)

# Time to wait for the model server to come up post-spawn. The first
# call has to load weights (sentence-transformers ~110 MB + cross-
# encoder ~80 MB from the HF cache, longer on a cold first run), so
# this is generous. We only block on /health (instant once the app
# is up) — actual model loading happens lazily on the first /embed
# or /rerank call.
_DEFAULT_READY_TIMEOUT_S = 30.0
_PROBE_INTERVAL_S = 0.3
_TERMINATE_GRACE_S = 5.0


def _tcp_reachable(host: str, port: int, *, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _http_health_ok(host: str, port: int, *, timeout_s: float = 1.0) -> bool:
    try:
        resp = httpx.get(
            f"http://{host}:{port}/health", timeout=timeout_s,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def ensure_running(
    *, host: str, port: int,
    ready_timeout_s: float = _DEFAULT_READY_TIMEOUT_S,
) -> subprocess.Popen[bytes] | None:
    """Ensure the model server is running on host:port, spawning if needed.

    Returns the subprocess handle if we spawned one (caller must call
    :func:`terminate_subprocess` at shutdown), or ``None`` when the
    server was already up or the spawn failed gracefully.
    """
    if _http_health_ok(host, port):
        _LOG.info(
            "modelserver already reachable at %s:%d; not spawning", host, port,
        )
        return None

    cmd = [sys.executable, "-m", "paperhub.modelserver"]
    # Propagate the host/port the caller asked for, so the spawned
    # process binds the same address we'll probe. Otherwise the child
    # re-reads Settings from its own env and may pick a different
    # default port — most commonly seen when ensure_running was passed
    # an ephemeral port for a smoke test.
    env = {
        **os.environ,
        "PAPERHUB_MODEL_SERVER_HOST": host,
        "PAPERHUB_MODEL_SERVER_PORT": str(port),
    }
    _LOG.info("modelserver spawning: %s (port=%d)", " ".join(cmd), port)
    # Detach the child from the parent so uvicorn --reload (which
    # kills the worker's process group on restart) doesn't take the
    # modelserver down with it. The whole point of isolation is for
    # the modelserver to OUTLIVE the worker across reloads.
    #   - Windows: CREATE_NEW_PROCESS_GROUP detaches the child from
    #     the parent's group so its Ctrl-C / kill doesn't cascade.
    #   - Unix: start_new_session=True calls setsid().
    # subprocess.Popen (sync) is intentional — the spawn call returns
    # immediately after fork/spawn; only the /health poll below is
    # async (via asyncio.sleep).
    # Platform-specific detach flag. Split into two Popen call sites
    # (rather than **kwargs unpacking) because Popen's overload set is
    # very narrow on those kwargs and mypy can't unify them through a
    # generic dict.
    try:
        # stdout/stderr go to DEVNULL — without that, the spawned
        # uvicorn would dump its logs into our pipe and we'd never
        # drain them, eventually blocking on a full pipe buffer.
        # Operators who want logs use `scripts/start.ps1` which runs
        # the modelserver in the foreground with its stdout visible.
        if sys.platform == "win32":
            proc = subprocess.Popen(  # noqa: ASYNC220, S603 — see note above
                cmd, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            proc = subprocess.Popen(  # noqa: ASYNC220, S603 — see note above
                cmd, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except (OSError, FileNotFoundError) as exc:
        _LOG.warning(
            "modelserver spawn failed (%s: %s); set "
            "PAPERHUB_INPROCESS_MODELS=1 to load models in the worker",
            type(exc).__name__, exc,
        )
        return None

    # Poll /health until ready or timeout. Use non-blocking sleep so
    # this stays compatible with lifespan's async context.
    deadline = time.monotonic() + ready_timeout_s
    while time.monotonic() < deadline:
        if _http_health_ok(host, port):
            _LOG.info(
                "modelserver ready at %s:%d (pid=%d)",
                host, port, proc.pid,
            )
            return proc
        if proc.poll() is not None:
            # Subprocess exited before becoming reachable. stdout is
            # DEVNULL (detachment requirement) so we can't echo the
            # child's logs here — operators should re-run with
            # `uv run paperhub-modelserver` directly to see the
            # failure, or use `scripts/start.ps1` which captures the
            # subprocess output.
            _LOG.warning(
                "modelserver subprocess exited prematurely (code=%s); "
                "run `uv run paperhub-modelserver` directly to see why, "
                "or set PAPERHUB_INPROCESS_MODELS=1 to bypass",
                proc.returncode,
            )
            return None
        await asyncio.sleep(_PROBE_INTERVAL_S)

    _LOG.warning(
        "modelserver did not become reachable on %s:%d within %.1fs; "
        "terminating subprocess",
        host, port, ready_timeout_s,
    )
    terminate_subprocess(proc)
    return None


def terminate_subprocess(proc: subprocess.Popen[bytes] | None) -> None:
    """Best-effort terminate. SIGTERM → wait → SIGKILL fallback."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=_TERMINATE_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        try:
            proc.wait(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            _LOG.warning(
                "modelserver subprocess (pid=%d) did not exit after SIGKILL",
                proc.pid,
            )
    except OSError as exc:
        _LOG.warning("modelserver subprocess teardown failed: %s", exc)
