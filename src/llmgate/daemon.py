"""Daemon lifecycle management for the gateway server."""

import os
import time
import json
import subprocess
import sys
from pathlib import Path

import httpx


class DaemonManager:
    """Manages the gateway daemon process lifecycle.

    The TUI uses this to start, stop, and check the status of the gateway daemon.
    """

    def __init__(self, config_dir: str | None = None):
        if config_dir:
            self.dir = Path(config_dir)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            self.dir = Path(xdg) / "llmgate"
        self.pid_path = self.dir / "daemon.pid"

    def is_running(self) -> bool:
        """Check if the daemon is currently running."""
        if not self.pid_path.exists():
            return False
        try:
            data = json.loads(self.pid_path.read_text())
            pid = data.get("pid")
            if pid is None:
                return False
            os.kill(pid, 0)
            return True
        except (ValueError, OSError, json.JSONDecodeError):
            return False

    def get_control_port(self) -> int | None:
        """Get the control API port from PID file."""
        if not self.pid_path.exists():
            return None
        try:
            data = json.loads(self.pid_path.read_text())
            return data.get("control_port")
        except (json.JSONDecodeError, ValueError):
            return None

    def get_status(self) -> dict:
        """Get daemon status via control API."""
        port = self.get_control_port()
        if port is None:
            return {"running": False}
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
        port = self.get_control_port()
        if port is None:
            return {"running": False}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/api/status")
                if resp.status_code == 200:
                    return {"running": True, **resp.json()}
        except Exception:
            pass
        return {"running": True, "error": "Cannot reach control API"}

    def start(self, port: int | None = None) -> None:
        """Start the daemon as a background subprocess."""
        if self.is_running():
            return
        import random
        control_port = port or random.randint(20000, 30000)
        self.dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["LLMGATE_CONTROL_PORT"] = str(control_port)
        cmd = [
            sys.executable, "-m", "llmgate",
            "--daemon",
        ]
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from TUI
        )
        # Write PID info
        self.pid_path.write_text(json.dumps({
            "pid": proc.pid,
            "control_port": control_port,
            "started_at": time.time(),
        }))
        # Give it a moment to start
        time.sleep(0.5)

    def stop(self) -> None:
        """Stop the daemon via control API and wait for process exit before
        cleaning up the PID file."""
        port = self.get_control_port()
        pid = None
        if port:
            try:
                with httpx.Client(timeout=5.0) as client:
                    client.post(f"http://127.0.0.1:{port}/api/daemon/stop")
            except Exception:
                pass
        # Read PID before unlinking, so we can verify the process stopped
        if self.pid_path.exists():
            try:
                data = json.loads(self.pid_path.read_text())
                pid = data.get("pid")
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        if pid:
            # Poll for process termination (up to ~5 s)
            for _ in range(10):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except OSError:
                    break
        if self.pid_path.exists():
            self.pid_path.unlink()

    def restart(self) -> None:
        """Restart the daemon."""
        self.stop()
        time.sleep(1.0)
        self.start()


def run_daemon() -> None:
    """Entry point for daemon mode. Called when llmgate is run with --daemon."""
    from llmgate.config.store import ConfigStore
    from llmgate.gateway.server import run_daemon as _server_run_daemon

    store = ConfigStore()
    if not store.key_path.exists():
        store.init_first_run()
    _server_run_daemon(store)
