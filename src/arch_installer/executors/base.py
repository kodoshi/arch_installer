"""the executor contract and the shell helpers executors share.

an executor implements one section of the installation: the commands it runs
plus the templates for the files it writes. it receives the fully resolved
InstallerConfig and decides nothing about *whether* it runs; the orchestrator does.
"""

from abc import ABC, abstractmethod

from arch_installer.config.models import CRYPTROOT_MAPPER_NAME, InstallerConfig
from arch_installer.core.command import CommandRunner

TARGET_ROOT = "/mnt"
TARGET_EFI = f"{TARGET_ROOT}/efi"

# sbctl moved its data directory from /usr/share/secureboot to /var/lib/sbctl
SBCTL_DIRECTORY = "/var/lib/sbctl"
SBCTL_LEGACY_DIRECTORY = "/usr/share/secureboot"
SBCTL_PK_KEY = f"{SBCTL_DIRECTORY}/keys/PK/PK.key"
SBCTL_DB_KEY = f"{SBCTL_DIRECTORY}/keys/db/db.key"


class Executor(ABC):
    def __init__(self, config: InstallerConfig, runner: CommandRunner) -> None:
        self._config = config
        self._runner = runner

    @abstractmethod
    def execute(self) -> None: ...


def is_mountpoint(runner: CommandRunner, path: str) -> bool:
    return runner.run(f"mountpoint -q {path}", raise_on_nonzero_exit=False).success


def path_exists(runner: CommandRunner, path: str) -> bool:
    return runner.run(f"test -e {path}", raise_on_nonzero_exit=False).success


def file_exists(runner: CommandRunner, path: str) -> bool:
    return runner.run(f"test -f {path}", raise_on_nonzero_exit=False).success


def directory_exists(runner: CommandRunner, path: str) -> bool:
    return runner.run(f"test -d {path}", raise_on_nonzero_exit=False).success


def write_file(runner: CommandRunner, path: str, content: str) -> None:
    # content goes through stdin, so it never needs shell quoting or heredoc delimiters
    runner.run(f"cat > {path}", input_data=content)


def detect_luks_uuid(runner: CommandRunner, root_partition: str) -> str | None:
    # the open mapping is authoritative; blkid lookups are fallbacks
    result = runner.run(f"cryptsetup status {CRYPTROOT_MAPPER_NAME}", raise_on_nonzero_exit=False)
    if result.success:
        for line in result.stdout.split("\n"):
            if "device:" in line:
                backing_device = line.split()[-1]
                uuid = runner.run(
                    f"blkid -s UUID -o value {backing_device}", raise_on_nonzero_exit=False
                ).stdout.strip()
                if uuid:
                    return uuid

    any_luks_uuids = runner.run(
        "blkid -t TYPE=crypto_LUKS -s UUID -o value", raise_on_nonzero_exit=False
    ).stdout.strip()
    if any_luks_uuids:
        return any_luks_uuids.split("\n")[0]

    root_uuid = runner.run(
        f"blkid -s UUID -o value {root_partition}", raise_on_nonzero_exit=False
    ).stdout.strip()
    return root_uuid or None
