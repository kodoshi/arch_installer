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
        work_dir: str | None = None,
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
        work_dir: str | None = None,
        input_data: str | None = None,
    ) -> CommandExecutionResult:
        shell = isinstance(command, str)
        cmd_str = command if isinstance(command, str) else " ".join(command)
        # stdin is never logged: it carries passwords and file contents
        logger.debug("$ %s", cmd_str)

        try:
            result = subprocess.run(
                command,
                shell=shell,
                capture_output=capture_output,
                text=True,
                env=dict(env_variables) if env_variables else None,
                cwd=work_dir,
                input=input_data,
            )
        except FileNotFoundError as e:
            raise CommandError(
                command=cmd_str,
                exit_code=127,
                stdout="",
                stderr=f"Command not found: {e}",
            ) from e

        cmd_result = CommandExecutionResult(
            command=cmd_str,
            exit_code=result.returncode,
            stdout=result.stdout if capture_output else "",
            stderr=result.stderr if capture_output else "",
        )
        if cmd_result.stdout:
            logger.debug("%s", cmd_result.stdout.rstrip())
        if cmd_result.stderr:
            logger.debug("stderr: %s", cmd_result.stderr.rstrip())

        if raise_on_nonzero_exit and not cmd_result.success:
            raise CommandError(
                command=cmd_str,
                exit_code=cmd_result.exit_code,
                stdout=cmd_result.stdout,
                stderr=cmd_result.stderr,
            )

        return cmd_result

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
        cmd_str = command if isinstance(command, str) else " ".join(command)
        return self.run(
            f"arch-chroot {chroot_path} {cmd_str}",
            raise_on_nonzero_exit=raise_on_nonzero_exit,
            capture_output=capture_output,
            env_variables=env_variables,
            input_data=input_data,
        )
