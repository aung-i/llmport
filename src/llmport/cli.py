"""CLI entry point for llmport."""

import sys
import argparse
import json
import urllib.request

from llmport.daemon import DaemonManager, run_daemon


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llmport",
        description="Terminal LLM API Gateway",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as daemon (internal use)",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version="%(prog)s 0.1.0",
        help="Show version and exit",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=["stop", "status"],
        help="Control the gateway daemon",
    )

    args = parser.parse_args()

    if args.daemon:
        run_daemon()
        return

    dm = DaemonManager()

    if args.action == "stop":
        if dm.is_running():
            dm.stop()
            print("Gateway stopped.")
        else:
            print("Gateway is not running.")
        return

    if args.action == "status":
        if dm.is_running():
            control_port = dm.get_control_port()
            status = dm.get_status()

            host, gw_port = "127.0.0.1", 11434
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{control_port}/api/gateway/config", timeout=3
                ) as resp:
                    cfg = json.loads(resp.read())
                    host = cfg.get("host", "127.0.0.1")
                    gw_port = cfg.get("port", 11434)
            except Exception:
                pass

            print(f"Gateway running on http://{host}:{gw_port}")
            print(f"  /openai/v1/*    → OpenAI")
            print(f"  /anthropic/v1/* → Anthropic")
            print(f"  /api/*          → Control")
            print(f"")
            print(f"  Active model: {status.get('active_model') or 'none'}")
            print(f"  Uptime:      {status.get('uptime', 0):.0f}s")
            print(f"  Requests:    {status.get('request_count', 0)}")
        else:
            print("Gateway is not running.")
        return

    # Default: launch TUI
    from llmport.app import LlmPortApp
    app = LlmPortApp()
    app.run()
