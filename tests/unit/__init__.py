"""FakeCommandRunner, which records commands instead of running them."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from arch_installer.core.command import CommandExecutionResult, CommandRunner
from arch_installer.errors import CommandError


@dataclass
class RecordedCommand:
    command: str
    raise_on_nonzero_exit: bool
    capture_output: bool
    env_variables: Mapping[str, str] | None
    working_directory: str | None
    input_data: str | None
    is_chroot: bool = False
    chroot_path: str | None = None


@dataclass
class FakeCommandRunner(CommandRunner):
    """Patterns match as substrings of the command. Handlers are tried first, then
    responses in the order they were set; the first match wins, and a command that
    matches nothing gets the default response."""

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
        self._responses[pattern] = (exit_code, stdout, stderr)

    def set_default_response(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self._default_response = (exit_code, stdout, stderr)

    def set_handler(
        self,
        pattern: str,
        handler: Callable[[str], CommandExecutionResult],
    ) -> None:
        self._handlers[pattern] = handler

    def run(
        self,
        command: str | list[str],
        *,
        raise_on_nonzero_exit: bool = True,
        capture_output: bool = True,
        env_variables: Mapping[str, str] | None = None,
        working_directory: str | None = None,
        input_data: str | None = None,
    ) -> CommandExecutionResult:
        command_text = command if isinstance(command, str) else " ".join(command)

        # Record the command
        self.recorded_commands.append(
            RecordedCommand(
                command=command_text,
                raise_on_nonzero_exit=raise_on_nonzero_exit,
                capture_output=capture_output,
                env_variables=dict(env_variables) if env_variables else None,
                working_directory=working_directory,
                input_data=input_data,
            )
        )

        # Check for custom handler
        for pattern, handler in self._handlers.items():
            if pattern in command_text:
                return handler(command_text)

        # Find matching response
        exit_code, stdout, stderr = self._default_response
        for pattern, response in self._responses.items():
            if pattern in command_text:
                exit_code, stdout, stderr = response
                break

        result = CommandExecutionResult(
            command=command_text,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

        if raise_on_nonzero_exit and not result.success:
            raise CommandError(
                command=command_text,
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
        command_text = command if isinstance(command, str) else " ".join(command)
        full_command = f"arch-chroot {chroot_path} {command_text}"

        # Record as chroot command
        self.recorded_commands.append(
            RecordedCommand(
                command=full_command,
                raise_on_nonzero_exit=raise_on_nonzero_exit,
                capture_output=capture_output,
                env_variables=dict(env_variables) if env_variables else None,
                working_directory=None,
                input_data=input_data,
                is_chroot=True,
                chroot_path=chroot_path,
            )
        )

        # Check for custom handler
        for pattern, handler in self._handlers.items():
            if pattern in command_text or pattern in full_command:
                return handler(full_command)

        # Find matching response (check both command and full chroot command)
        exit_code, stdout, stderr = self._default_response
        for pattern, response in self._responses.items():
            if pattern in command_text or pattern in full_command:
                exit_code, stdout, stderr = response
                break

        result = CommandExecutionResult(
            command=full_command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

        if raise_on_nonzero_exit and not result.success:
            raise CommandError(
                command=full_command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

        return result

    def get_commands(self, pattern: str | None = None) -> list[str]:
        commands = [r.command for r in self.recorded_commands]
        if pattern:
            commands = [c for c in commands if pattern in c]
        return commands

    def written_content(self, path: str) -> str:
        writes = [r for r in self.recorded_commands if r.command == f"cat > {path}"]
        if not writes:
            raise AssertionError(
                f"Expected {path} to be written.\nRecorded commands: {self.get_commands()}"
            )
        return writes[-1].input_data or ""

    def assert_command_called(self, pattern: str) -> None:
        if not self.get_commands(pattern):
            raise AssertionError(
                f"Expected command matching '{pattern}' to be called.\n"
                f"Recorded commands: {self.get_commands()}"
            )

    def assert_command_not_called(self, pattern: str) -> None:
        if self.get_commands(pattern):
            raise AssertionError(
                f"Expected command matching '{pattern}' NOT to be called.\n"
                f"But found: {self.get_commands(pattern)}"
            )

    def clear(self) -> None:
        self.recorded_commands.clear()
