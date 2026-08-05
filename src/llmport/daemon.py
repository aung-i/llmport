"""Daemon lifecycle management for the gateway server.

The gateway runs as a background subprocess serving a single Starlette app
on one loopback port. Both the protocol-forwarding routes (``/openai/v1/*``,
``/anthropic/v1/*``, ``/v1/*``) and the control API (``/api/*``) live on that
same port, so there is no separate control port to discover.
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

# How long start() waits for the gateway to answer /api/status before giving up.
_START_TIMEOUT_SECONDS = 10.0

# The gateway is loopback-only by design. No matter what config.yaml says, the
# daemon never binds a non-loopback interface (the old control-API host check
# was the only enforcement; it moved here so hand-edited configs can't expose
# the gateway either).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _loopback_host(host: str) -> str:
    """Return *host* if it is loopback, else ``127.0.0.1``."""
    return host if host in _LOOPBACK_HOSTS else "127.0.0.1"


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
            gw = self.store.load_config().get("gateway") or {}
            return int(gw.get("port", DEFAULT_PORT))
        except Exception:
            return DEFAULT_PORT

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check if the daemon is currently running."""
        pid = self._read_pid_field("pid")
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ValueError, OSError):
            return False

    def get_control_port(self) -> int | None:
        """Return the gateway/control port (single port) when running."""
        if not self.is_running():
            return None
        return self._gateway_port()

    def get_status(self) -> dict:
        """Get daemon status via control API."""
        if not self.is_running():
            return {"running": False}
        port = self._gateway_port()
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"http://127.0.0.1:{port}/api/status")
                if resp.status_code == 200:
                    return {"running": True, **resp.json()}
        except Exception:
            pass
        return {"running": True, "error": "Cannot reach control API"}

    async def async_get_status(self) -> dict:
        """Async version of get_status."""
        if not self.is_running():
            return {"running": False}
        port = self._gateway_port()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/api/status")
                if resp.status_code == 200:
                    return {"running": True, **resp.json()}
        except Exception:
            pass
        return {"running": True, "error": "Cannot reach control API"}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the daemon as a background subprocess and wait until ready.

        Returns True if the gateway answered ``/api/status`` within the
        startup timeout, False otherwise.
        """
        if self.is_running():
            return True
        self.store.init_first_run()  # ensure config + run any migration
        cmd = [sys.executable, "-m", "llmport", "--daemon"]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from the caller
        )
        # Wait for the gateway to answer on its port.
        port = self._gateway_port()
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(
                        f"http://127.0.0.1:{port}/api/status"
                    ).status_code == 200:
                        # Confirm it is OUR daemon (PID file present + alive),
                        # not another process squatting on the port.
                        return self.is_running()
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        """Stop the daemon: ask the control API, then escalate to signals."""
        pid = self._read_pid_field("pid")
        if pid is None:
            self._cleanup_pid()
            return

        port = self._gateway_port()
        # 1. Ask the control API to shut down gracefully.
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(f"http://127.0.0.1:{port}/api/daemon/stop")
        except Exception:
            pass
        if self._wait_for_exit(pid, 6.0):
            self._cleanup_pid()
            return

        # 2. SIGTERM (uvicorn handles it gracefully).
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError:
            pass
        if self._wait_for_exit(pid, 5.0):
            self._cleanup_pid()
            return

        # 3. SIGKILL as a last resort.
        try:
            os.kill(pid, 9)  # SIGKILL
        except OSError:
            pass
        self._wait_for_exit(pid, 2.0)
        self._cleanup_pid()

    def restart(self) -> bool:
        """Restart the daemon. Returns True if it came back up."""
        self.stop()
        time.sleep(0.5)
        return self.start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wait_for_exit(self, pid: int, timeout: float) -> bool:
        """Poll until the process is gone. Returns True if it exited."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
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


def run_daemon() -> None:
    """Entry point for daemon mode (``llmport --daemon``).

    Runs a single uvicorn server on the configured loopback host/port. The
    PID file is written so the CLI's status/stop commands work, and removed
    on exit.
    """
    import uvicorn

    from llmport.gateway.server import create_app
    from llmport.gateway import control_api
    from llmport.gateway.state import migrate_gateway_config

    store = ConfigStore()
    store.init_first_run()  # handles first-run create + legacy migration

    gw = migrate_gateway_config(store.load_config())
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
    control_api.set_shutdown_server(server)

    try:
        server.run()
    finally:
        try:
            pid_path.unlink()
        except OSError:
            pass
