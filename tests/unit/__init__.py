"""Test fixtures and fakes for unit testing.

Provides fake implementations of CommandRunner for testing
without executing actual system commands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable

from arch_installer.core.command import CommandExecutionResult, CommandRunner


@dataclass
class RecordedCommand:
    """A command that was recorded by the fake runner."""

    command: str
    raise_on_nonzero_exit: bool
    capture_output: bool
    env_variables: Mapping[str, str] | None
    work_dir: str | None
    input_data: str | None
    is_chroot: bool = False
    chroot_path: str | None = None


@dataclass
class FakeCommandRunner(CommandRunner):
    """Fake command runner for testing.

    Records all commands and returns configurable responses.
    Allows setting up expectations for specific commands.
    """

    # Recorded commands for assertion
    recorded_commands: list[RecordedCommand] = field(default_factory=list)

    # Command responses: command pattern -> (exit_code, stdout, stderr)
    _responses: dict[str, tuple[int, str, str]] = field(default_factory=dict)

    # Default response for unmatched commands
    _default_response: tuple[int, str, str] = (0, "", "")

    # Custom handlers: command pattern -> handler function
    _handlers: dict[str, Callable[[str], CommandExecutionResult]] = field(default_factory=dict)

    def set_response(
        self,
        pattern: str,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Set response for commands matching pattern."""
        self._responses[pattern] = (exit_code, stdout, stderr)

    def set_default_response(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Set default response for unmatched commands."""
        self._default_response = (exit_code, stdout, stderr)

    def set_handler(
        self,
        pattern: str,
        handler: Callable[[str], CommandExecutionResult],
    ) -> None:
        """Set custom handler for commands matching pattern."""
        self._handlers[pattern] = handler

    def run(
        self,
        command: str | list[str],
        *,
        raise_on_nonzero_exit: bool = True,
        capture_output: bool = True,
        env_variables: Mapping[str, str] | None = None,
        work_dir: str | None = None,
        input_data: str | None = None,
    ) -> CommandExecutionResult:
        """Execute (fake) a command and return configured result."""
        from arch_installer.errors import CommandError

        cmd_str = command if isinstance(command, str) else " ".join(command)

        # Record the command
        self.recorded_commands.append(
            RecordedCommand(
                command=cmd_str,
                raise_on_nonzero_exit=raise_on_nonzero_exit,
                capture_output=capture_output,
                env_variables=dict(env_variables) if env_variables else None,
                work_dir=work_dir,
                input_data=input_data,
            )
        )

        # Check for custom handler
        for pattern, handler in self._handlers.items():
            if pattern in cmd_str:
                return handler(cmd_str)

        # Find matching response
        exit_code, stdout, stderr = self._default_response
        for pattern, response in self._responses.items():
            if pattern in cmd_str:
                exit_code, stdout, stderr = response
                break

        result = CommandExecutionResult(
            command=cmd_str,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

        if raise_on_nonzero_exit and not result.success:
            raise CommandError(
                command=cmd_str,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

        return result

    def run_as_chroot(
        self,
        command: str | list[str],
        chroot_path: str = "/mnt",
        *,
        raise_on_nonzero_exit: bool = True,
        capture_output: bool = True,
        env_variables: Mapping[str, str] | None = None,
        input_data: str | None = None,
    ) -> CommandExecutionResult:
        """Execute (fake) a chroot command."""
        from arch_installer.errors import CommandError

        cmd_str = command if isinstance(command, str) else " ".join(command)
        full_cmd = f"arch-chroot {chroot_path} {cmd_str}"

        # Record as chroot command
        self.recorded_commands.append(
            RecordedCommand(
                command=full_cmd,
                raise_on_nonzero_exit=raise_on_nonzero_exit,
                capture_output=capture_output,
                env_variables=dict(env_variables) if env_variables else None,
                work_dir=None,
                input_data=input_data,
                is_chroot=True,
                chroot_path=chroot_path,
            )
        )

        # Check for custom handler
        for pattern, handler in self._handlers.items():
            if pattern in cmd_str or pattern in full_cmd:
                return handler(full_cmd)

        # Find matching response (check both command and full chroot command)
        exit_code, stdout, stderr = self._default_response
        for pattern, response in self._responses.items():
            if pattern in cmd_str or pattern in full_cmd:
                exit_code, stdout, stderr = response
                break

        result = CommandExecutionResult(
            command=full_cmd,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

        if raise_on_nonzero_exit and not result.success:
            raise CommandError(
                command=full_cmd,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

        return result

    def get_commands(self, pattern: str | None = None) -> list[str]:
        """Get recorded commands, optionally filtered by pattern."""
        commands = [r.command for r in self.recorded_commands]
        if pattern:
            commands = [c for c in commands if pattern in c]
        return commands

    def assert_command_called(self, pattern: str) -> None:
        """Assert that a command matching pattern was called."""
        if not self.get_commands(pattern):
            raise AssertionError(
                f"Expected command matching '{pattern}' to be called.\n"
                f"Recorded commands: {self.get_commands()}"
            )

    def assert_command_not_called(self, pattern: str) -> None:
        """Assert that no command matching pattern was called."""
        if self.get_commands(pattern):
            raise AssertionError(
                f"Expected command matching '{pattern}' NOT to be called.\n"
                f"But found: {self.get_commands(pattern)}"
            )

    def clear(self) -> None:
        """Clear recorded commands."""
        self.recorded_commands.clear()
