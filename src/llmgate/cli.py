"""CLI entry point for llmgate."""

import sys
import argparse

from llmgate.daemon import DaemonManager, run_daemon


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llmgate",
        description="Terminal LLM API Gateway",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as daemon (internal use)",
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
            status = dm.get_status()
            print(f"Gateway running on port {dm.get_control_port()}")
            print(f"Active model: {status.get('active_model', 'none')}")
            print(f"Uptime: {status.get('uptime', 0):.0f}s")
            print(f"Requests: {status.get('request_count', 0)}")
        else:
            print("Gateway is not running.")
        return

    # Default: launch TUI
    from llmgate.app import LlmGateApp
    app = LlmGateApp()
    app.run()
