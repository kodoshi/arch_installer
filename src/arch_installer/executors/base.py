"""the executor contract and the shell helpers executors share.

an executor implements one section of the installation: the commands it runs
plus the templates for the files it writes. it receives the fully resolved
InstallerConfig and decides nothing about *whether* it runs; the orchestrator does.
"""

from abc import ABC, abstractmethod

from arch_installer.config.models import InstallerConfig
from arch_installer.core.command import CommandRunner

TARGET_ROOT = "/mnt"
TARGET_EFI = f"{TARGET_ROOT}/efi"

# sbctl moved its data directory from /usr/share/secureboot to /var/lib/sbctl
SBCTL_DIRECTORY = "/var/lib/sbctl"
SBCTL_LEGACY_DIRECTORY = "/usr/share/secureboot"
SBCTL_PK_KEY = f"{SBCTL_DIRECTORY}/keys/PK/PK.key"
SBCTL_DB_KEY = f"{SBCTL_DIRECTORY}/keys/db/db.key"


class StepExecutor(ABC):
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


# the header holds the UUID, so this works for a header on the partition or detached from it
def detect_luks_uuid(runner: CommandRunner, luks_header_device: str) -> str:
    result = runner.run(f"cryptsetup luksUUID {luks_header_device}", raise_on_nonzero_exit=False)
    return result.stdout.strip() if result.success else ""


def partition_uuid(runner: CommandRunner, partition: str) -> str:
    uuid = runner.run(
        f"blkid -s PARTUUID -o value {partition}", raise_on_nonzero_exit=False
    ).stdout.strip()
    if not uuid:
        raise RuntimeError(f"{partition} has no GPT partition UUID")
    return uuid


# hardware identifiers burnt into the device, unique worldwide
UNIQUE_DISK_IDENTIFIER_PREFIXES = ("wwn-", "nvme-eui.")


# a disk without a partition table has no partition UUID, so the boot names it by the
# /dev/disk/by-id link udev derives from its hardware (model and serial, WWN or EUI)
def stable_disk_path(runner: CommandRunner, disk: str) -> str:
    links = runner.run(
        f"target=$(readlink -f {disk}); for link in /dev/disk/by-id/*; do "
        f'[ "$(readlink -f "$link")" = "$target" ] && echo "$link"; done',
        raise_on_nonzero_exit=False,
    ).stdout.split()
    unique = [
        link
        for link in links
        if link.rsplit("/", 1)[-1].startswith(UNIQUE_DISK_IDENTIFIER_PREFIXES)
    ]
    return sorted(unique or links)[0] if links else ""
