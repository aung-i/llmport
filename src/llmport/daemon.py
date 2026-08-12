"""Daemon lifecycle management for the gateway server.

The gateway runs as a background subprocess serving a single Starlette app
on one loopback port: protocol-forwarding routes (``/openai/v1/*``,
``/anthropic/v1/*``) plus a read-only ``/health`` probe. Lifecycle control
(stop / restart) is via process signals, not HTTP.
"""

import os
import time
import json
import subprocess
import sys
from pathlib import Path

import httpx

from llmport.config.store import ConfigStore

DEFAULT_PORT = 11434

# How long start() waits for the gateway to answer /health before giving up.
_START_TIMEOUT_SECONDS = 10.0

# The gateway is loopback-only by design. No matter what config.yaml says, the
# daemon never binds a non-loopback interface (the old control-API host check
# was the only enforcement; it moved here so hand-edited configs can't expose
# the gateway either).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _loopback_host(host: str) -> str:
    """Return *host* if it is loopback, else ``127.0.0.1``."""
    return host if host in _LOOPBACK_HOSTS else "127.0.0.1"


def resolve_gateway(store, cli_host: str | None = None,
                    cli_port: int | None = None) -> dict:
    """Resolve gateway ``{"host", "port"}``: CLI args > config.yaml > default.

    The caller (``run_daemon``) forces the host to loopback afterwards. No
    environment-variable layer -- gateway is configured via the CLI
    (``llmport start --host/--port``) or the ``gateway:`` section of
    ``config.yaml``.
    """
    gw = store.load_gateway()  # {host, port} from config.yaml or default
    host = cli_host if cli_host else gw["host"]
    port = cli_port if cli_port else gw["port"]
    return {"host": host, "port": int(port)}


def _argv_flag_value(flag: str) -> str | None:
    """Return the value following *flag* in ``sys.argv``.

    Supports ``--flag value`` and ``--flag=value``. Used by ``run_daemon`` to
    pick up ``--host``/``--port`` that ``llmport start`` passes to the daemon
    subprocess -- the eager ``--daemon`` callback exits before Typer binds
    them, so argv is read directly.
    """
    import sys
    args = sys.argv
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


class DaemonManager:
    """Manages the gateway daemon process lifecycle."""

    def __init__(self, config_dir: str | None = None):
        self.store = ConfigStore(config_dir)
        self.dir = self.store.dir
        self.pid_path = self.dir / "daemon.pid"

    # ------------------------------------------------------------------
    # PID file helpers
    # ------------------------------------------------------------------

    def _read_pid_field(self, field: str):
        if not self.pid_path.exists():
            return None
        try:
            return json.loads(self.pid_path.read_text()).get(field)
        except (json.JSONDecodeError, ValueError, OSError):
            return None

    def _gateway_port(self) -> int:
        """Best-effort gateway port: PID file, then config, then default."""
        port = self._read_pid_field("port")
        if port:
            return int(port)
        try:
            return int(self.store.load_gateway()["port"])
        except Exception:
            return DEFAULT_PORT

    def _process_cmdline(self, pid: int) -> str | None:
        """Return process *pid*'s command line, or None if it can't be read.

        Uses ``ps`` (portable across macOS/Linux). None means the process is
        dead or ``ps`` is unavailable -- callers fall back to liveness only.
        """
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=3.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _pid_is_our_daemon(self, pid: int) -> bool:
        """True if *pid* is alive AND is our ``llmport --daemon`` process.

        Verifies identity so a recycled pid owned by an unrelated process is
        NOT mistaken for our daemon -- otherwise ``stop`` could signal (kill)
        the wrong process.

        POSIX: liveness via ``os.kill(pid, 0)`` + command-line identity via
        ``ps`` (portable across macOS/Linux). If the command line can't be
        read (``ps`` unavailable), falls back to trusting liveness alone --
        never worse than the old ``os.kill``-only behavior.

        Windows: ``os.kill(pid, 0)`` and ``ps`` aren't available, so identity
        comes from the daemon self-reporting its pid at ``GET /health`` -- if
        the process serving on our gateway port reports this pid, it IS our
        daemon (stronger than a cmdline match, and immune to pid recycling).
        Trade-off: a hung daemon (alive but not answering /health) reads as
        "not running" and can't be stopped via the CLI -- kill it with
        ``taskkill /F /PID <pid>``.
        """
        if os.name == "nt":
            return self._health_reports_pid(pid)
        try:
            os.kill(pid, 0)
        except (ValueError, OSError):
            return False  # process is dead
        cmdline = self._process_cmdline(pid)
        if cmdline is None:
            return True  # alive, but can't verify -> trust (fallback)
        return "llmport" in cmdline and "--daemon" in cmdline

    def _health_reports_pid(self, pid: int) -> bool:
        """True if ``GET /health`` on the gateway port reports *pid*.

        The daemon serves its own ``os.getpid()`` at ``/health``, so a matching
        pid confirms the process on our port is our daemon -- cross-platform,
        no ``ps``/``os.kill`` needed. Used for identity on Windows.
        """
        port = self._gateway_port()
        try:
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(f"http://127.0.0.1:{port}/health")
                return resp.status_code == 200 and resp.json().get("pid") == pid
        except Exception:
            return False

    def _port_answers_health(self, port: int) -> bool:
        """True if something answers ``/health`` with 200 on localhost:port."""
        try:
            with httpx.Client(timeout=1.0) as client:
                return client.get(
                    f"http://127.0.0.1:{port}/health"
                ).status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check if *our* daemon is currently running.

        A pid that is dead (stale file) or recycled by an unrelated process is
        treated as not running, and the stale pid file is cleared so later
        commands don't act on a bogus pid.
        """
        pid = self._read_pid_field("pid")
        if pid is None:
            return False
        if not self._pid_is_our_daemon(pid):
            self._cleanup_pid()
            return False
        return True

    def get_control_port(self) -> int | None:
        """Return the gateway/control port (single port) when running."""
        if not self.is_running():
            return None
        return self._gateway_port()

    def started_at(self) -> float | None:
        """Return the daemon start time (epoch seconds) from the PID file."""
        return self._read_pid_field("started_at")

    def get_status(self) -> dict:
        """Get daemon liveness via /health."""
        if not self.is_running():
            return {"running": False}
        port = self._gateway_port()
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"http://127.0.0.1:{port}/health")
                if resp.status_code == 200:
                    return {"running": True, **resp.json()}
        except Exception:
            pass
        return {"running": True, "error": "Cannot reach /health"}

    async def async_get_status(self) -> dict:
        """Async version of get_status."""
        if not self.is_running():
            return {"running": False}
        port = self._gateway_port()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/health")
                if resp.status_code == 200:
                    return {"running": True, **resp.json()}
        except Exception:
            pass
        return {"running": True, "error": "Cannot reach /health"}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, host: str | None = None, port: int | None = None) -> bool:
        """Start the daemon as a background subprocess and wait until ready.

        ``host``/``port`` (from ``llmport start --host/--port``) override the
        ``gateway:`` section of ``providers.yaml`` for this run; they are
        passed to the daemon subprocess on its argv. Returns True if the
        gateway answered ``/health`` within the startup timeout.
        """
        if self.is_running():
            return True
        self.store.init_first_run()
        wait_port = port if port is not None else self._gateway_port()
        # Orphan guard: a daemon is already answering on the port but we have
        # no valid pid for it (e.g. daemon.pid was manually deleted while the
        # process kept running). Don't spawn a duplicate that can't bind --
        # tell the user to free the port instead.
        if self._port_answers_health(wait_port):
            print(
                f"端口 {wait_port} 已有进程响应 /health，但无有效 pid 文件"
                f"（可能是孤儿 daemon）。未启动重复实例；请释放该端口或用"
                f" --port 指定其它端口。"
            )
            return False
        cmd = [sys.executable, "-m", "llmport", "--daemon"]
        if host:
            cmd += ["--host", host]
        if port is not None:
            cmd += ["--port", str(port)]
        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            # Detach into a new process group so the daemon survives the CLI
            # exiting and ignores Ctrl-C in the console. (start_new_session is
            # POSIX-only and silently ignored on Windows.)
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        else:
            popen_kwargs["start_new_session"] = True  # detach from the caller
        subprocess.Popen(cmd, **popen_kwargs)
        # Wait for the gateway to answer on its port.
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(
                        f"http://127.0.0.1:{wait_port}/health"
                    ).status_code == 200:
                        # Confirm it is OUR daemon (PID file present + alive),
                        # not another process squatting on the port.
                        return self.is_running()
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        """Stop the daemon (SIGTERM then SIGKILL on POSIX; TerminateProcess on
        Windows).

        Control is exercised over the process, not over HTTP, so no control
        surface rides on the forwarding port. uvicorn handles SIGTERM with a
        graceful shutdown. The pid is identity-checked first: a stale or
        recycled pid is cleared without signaling, so an unrelated process can
        never be killed by mistake.
        """
        pid = self._read_pid_field("pid")
        if pid is None:
            self._cleanup_pid()
            return

        # Don't signal a pid that isn't ours (dead/stale, or recycled by an
        # unrelated process) -- just clear the stale pid file.
        if not self._pid_is_our_daemon(pid):
            self._cleanup_pid()
            return

        if os.name == "nt":
            # Windows: os.kill(pid, SIGTERM) maps to TerminateProcess -- an
            # immediate, unconditional kill (no graceful/forceful distinction,
            # so no SIGKILL escalation). ValueError/PermissionError -> gone.
            import signal
            try:
                os.kill(pid, signal.SIGTERM)
            except (ValueError, OSError):
                pass
            self._wait_for_exit(pid, 6.0)
            self._cleanup_pid()
            return

        # 1. SIGTERM (uvicorn handles it gracefully).
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError:
            pass
        if self._wait_for_exit(pid, 6.0):
            self._cleanup_pid()
            return

        # 2. SIGKILL as a last resort.
        try:
            os.kill(pid, 9)  # SIGKILL
        except OSError:
            pass
        self._wait_for_exit(pid, 2.0)
        self._cleanup_pid()

    def restart(self, host: str | None = None, port: int | None = None) -> bool:
        """Restart the daemon. Returns True if it came back up."""
        self.stop()
        time.sleep(0.5)
        return self.start(host=host, port=port)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wait_for_exit(self, pid: int, timeout: float) -> bool:
        """Poll until the process is gone. Returns True if it exited."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.name == "nt":
                # No os.kill(pid,0) liveness on Windows; poll /health instead
                # -- once the port stops answering, the daemon is gone.
                if not self._port_answers_health(self._gateway_port()):
                    return True
            else:
                try:
                    os.kill(pid, 0)
                except OSError:
                    return True
            time.sleep(0.25)
        return False

    def _cleanup_pid(self) -> None:
        if self.pid_path.exists():
            try:
                self.pid_path.unlink()
            except OSError:
                pass


def run_daemon(host: str | None = None, port: int | None = None) -> None:
    """Entry point for daemon mode (``llmport --daemon``).

    Runs a single uvicorn server on the configured loopback host/port. Gateway
    host/port resolution: CLI args (``host``/``port``) > ``providers.yaml`` >
    default; the host is always forced to loopback. The PID file is written so
    the CLI's status/stop commands work, and removed on exit.
    """
    import uvicorn

    from llmport.gateway.server import create_app

    store = ConfigStore()
    store.init_first_run()

    # Auth is mandatory: refuse to serve without an API key. This is the
    # backstop for direct `llmport --daemon` invocation that bypasses the
    # CLI `start` pre-check. `llmport setup` generates a key.
    if not store.load_api_key():
        print("llmport api_key 未设置（鉴权为强制）。请先运行: llmport setup",
              file=sys.stderr)
        sys.exit(1)

    # In production, host/port arrive on the daemon subprocess argv
    # (`llmport --daemon --host X --port Y`, set by `llmport start`). The
    # eager --daemon callback exits before Typer binds them, so read argv
    # directly. Tests pass host/port explicitly.
    if host is None:
        host = _argv_flag_value("--host")
    if port is None:
        p = _argv_flag_value("--port")
        port = int(p) if p else None

    gw = resolve_gateway(store, host, port)
    host = _loopback_host(gw["host"])
    port = gw["port"]

    # Write PID file: {pid, started_at, port}.
    store.dir.mkdir(parents=True, exist_ok=True)
    pid_path = store.dir / "daemon.pid"
    pid_path.write_text(json.dumps({
        "pid": os.getpid(),
        "started_at": time.time(),
        "port": port,
    }))

    app = create_app(store)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    try:
        server.run()
    finally:
        try:
            pid_path.unlink()
        except OSError:
            pass
