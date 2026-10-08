import logging
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from arch_installer.errors import CommandError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandExecutionResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()


class CommandRunner(ABC):
    @abstractmethod
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
        pass

    @abstractmethod
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
        pass


class SystemCommandRunner(CommandRunner):
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
        shell = isinstance(command, str)
        command_text = command if isinstance(command, str) else " ".join(command)
        # stdin is never logged: it carries passwords and file contents
        logger.debug("$ %s", command_text)

        try:
            result = subprocess.run(
                command,
                shell=shell,
                capture_output=capture_output,
                text=True,
                env=dict(env_variables) if env_variables else None,
                cwd=working_directory,
                input=input_data,
            )
        except FileNotFoundError as error:
            raise CommandError(
                command=command_text,
                exit_code=127,
                stdout="",
                stderr=f"Command not found: {error}",
            ) from error

        command_result = CommandExecutionResult(
            command=command_text,
            exit_code=result.returncode,
            stdout=result.stdout if capture_output else "",
            stderr=result.stderr if capture_output else "",
        )
        if command_result.stdout:
            logger.debug("%s", command_result.stdout.rstrip())
        if command_result.stderr:
            logger.debug("stderr: %s", command_result.stderr.rstrip())

        if raise_on_nonzero_exit and not command_result.success:
            raise CommandError(
                command=command_text,
                exit_code=command_result.exit_code,
                stdout=command_result.stdout,
                stderr=command_result.stderr,
            )

        return command_result

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
        return self.run(
            f"arch-chroot {chroot_path} {command_text}",
            raise_on_nonzero_exit=raise_on_nonzero_exit,
            capture_output=capture_output,
            env_variables=env_variables,
            input_data=input_data,
        )
