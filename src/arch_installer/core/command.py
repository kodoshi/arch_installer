import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from arch_installer.errors import CommandError


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
    """
    interface for executing system commands

    this abstraction allows for dependency injection in tests,
    enabling the use of fake/mock implementations without ugly patches
    """

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
        """
        execute a command and return the result
        """
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
        """
        execute a command in a chroot environment
        """
        pass


class SystemCommandRunner(CommandRunner):
    """CommandRunner implementation using subprocess"""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose

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
        if isinstance(command, str):
            shell = True
            cmd_str = command
        else:
            shell = False
            cmd_str = " ".join(command)

        if self.verbose:
            print(f">>>>> {cmd_str}")

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

        if self.verbose and capture_output:
            if cmd_result.stdout:
                print(cmd_result.stdout)
            if cmd_result.stderr:
                print(f"stderr: {cmd_result.stderr}")

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
        if isinstance(command, list):
            cmd_str = " ".join(command)
        else:
            cmd_str = command

        chroot_cmd = f"arch-chroot {chroot_path} {cmd_str}"
        return self.run(
            chroot_cmd,
            raise_on_nonzero_exit=raise_on_nonzero_exit,
            capture_output=capture_output,
            env_variables=env_variables,
            input_data=input_data,
        )
