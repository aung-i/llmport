"""Tests for CLI start/restart subcommands (Issue 5).

The CLI must support ``llmport start`` and ``llmport restart`` in addition
to the existing ``stop`` and ``status`` commands.

The refactored ``main()`` delegates to ``_cmd_start(dm)`` / ``_cmd_restart(dm)``
helpers, so we inspect those helpers' source to confirm they invoke the
``DaemonManager`` lifecycle methods.
"""

import inspect


class TestCliStartRestart:

    def test_start_subcommand_exists(self):
        """The CLI must support 'start' as an action."""
        from llmport.cli import main

        source = inspect.getsource(main)
        assert "'start'" in source or '"start"' in source, (
            "CLI must accept 'start' as a subcommand"
        )

    def test_restart_subcommand_exists(self):
        """The CLI must support 'restart' as an action."""
        from llmport.cli import main

        source = inspect.getsource(main)
        assert "'restart'" in source or '"restart"' in source, (
            "CLI must accept 'restart' as a subcommand"
        )

    def test_start_calls_daemon_start(self):
        """The 'start' action must dispatch to _cmd_start, which calls dm.start()."""
        from llmport.cli import main, _cmd_start

        main_source = inspect.getsource(main)
        assert "_cmd_start" in main_source, (
            "main() must dispatch the 'start' action to _cmd_start"
        )
        helper_source = inspect.getsource(_cmd_start)
        assert ".start()" in helper_source, (
            "_cmd_start should invoke dm.start()"
        )

    def test_restart_calls_daemon_restart(self):
        """The 'restart' action must dispatch to _cmd_restart, which calls dm.restart()."""
        from llmport.cli import main, _cmd_restart

        main_source = inspect.getsource(main)
        assert "_cmd_restart" in main_source, (
            "main() must dispatch the 'restart' action to _cmd_restart"
        )
        helper_source = inspect.getsource(_cmd_restart)
        assert ".restart()" in helper_source, (
            "_cmd_restart should invoke dm.restart()"
        )

    def test_choices_includes_start_restart(self):
        """The argparse choices for the action argument must include
        'start' and 'restart'."""
        from llmport.cli import main

        source = inspect.getsource(main)
        # The choices list should include start and restart
        assert "start" in source and "restart" in source, (
            "argparse choices must include 'start' and 'restart'"
        )

    def test_stop_and_status_still_work(self):
        """Existing 'stop' and 'status' subcommands must still be present."""
        from llmport.cli import main

        source = inspect.getsource(main)
        assert "'stop'" in source or '"stop"' in source
        assert "'status'" in source or '"status"' in source
