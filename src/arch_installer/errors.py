from dataclasses import dataclass


class ArchInstallerError(Exception):
    pass


@dataclass
class CommandError(ArchInstallerError):
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
    pass


class MigrationError(ArchInstallerError):
    pass


class UsbBootDriveError(ArchInstallerError):
    pass
