"""custom exceptions for the arch installer."""

from dataclasses import dataclass


class ArchInstallerError(Exception):
    """base exception for all installer errors."""

    pass


@dataclass
class CommandError(ArchInstallerError):
    """raised when a system command fails."""

    command: str
    exit_code: int
    stdout: str
    stderr: str

    def __str__(self) -> str:
        return (
            f"Command '{self.command}' failed with exit code {self.exit_code}\n"
            f"stdout: {self.stdout}\n"
            f"stderr: {self.stderr}"
        )


class ConfigurationError(ArchInstallerError):
    """raised when configuration is invalid or missing."""

    pass


class StorageError(ArchInstallerError):
    """raised when storage operations fail."""

    pass


class ValidationError(ArchInstallerError):
    """raised when validation fails."""

    pass
