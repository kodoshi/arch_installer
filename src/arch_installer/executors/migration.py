"""Migration from an existing encrypted btrfs Arch install.

Staging runs before the storage step wipes the disk, restoring after the packages step.
A staging failure stops the installation before the wipe.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from arch_installer.errors import MigrationError
from arch_installer.executors.base import (
    SBCTL_DIRECTORY,
    SBCTL_LEGACY_DIRECTORY,
    TARGET_ROOT,
    StepExecutor,
    directory_exists,
    path_exists,
)

logger = logging.getLogger(__name__)

STAGING_DIRECTORY = "/tmp/migration-staging"
OLD_MOUNT_DIRECTORY = "/tmp/old-system"
OLD_MAPPER_NAME = "oldcryptroot"
# room left free in the staging filesystem on top of the data itself
STAGING_HEADROOM_MB = 256


def path_in_old_root(path: str) -> str:
    # the old system's / lives in the @ subvolume of the btrfs top level
    return f"{OLD_MOUNT_DIRECTORY}/@/{path.lstrip('/')}"


@dataclass(frozen=True)
class ExistingInstallInfo:
    disk: str
    root_partition: str
    home_subvolume: str
    home_size_mb: int
    secure_boot_directory: str


class MigrationStagingStepExecutor(StepExecutor):
    def execute(self) -> None:
        disk = self._config.storage.target_disk
        luks_partition = self._find_luks_partition(disk)
        self._open_and_mount(luks_partition)
        try:
            existing = self._inspect_mounted_system(disk, luks_partition)
            logger.info(
                "Existing install on %s: home %sMB, secure boot keys: %s",
                existing.root_partition,
                existing.home_size_mb,
                existing.secure_boot_directory or "none",
            )
            self._ensure_staging_space(existing)
            self._copy_to_staging(existing)
        finally:
            self._unmount_and_close()
        logger.info("Migration staging complete")

    def _find_luks_partition(self, disk: str) -> str:
        listing = self._runner.run(f"lsblk -ln -o NAME {disk}", raise_on_nonzero_exit=False).stdout
        partitions = [
            f"/dev/{name.strip()}"
            for name in listing.split("\n")
            if name.strip() and name.strip() != Path(disk).name
        ]
        for partition in partitions:
            if self._runner.run(
                f"cryptsetup isLuks {partition}", raise_on_nonzero_exit=False
            ).success:
                return partition
        raise MigrationError(f"No LUKS partition found on {disk}, nothing to migrate")

    def _open_and_mount(self, partition: str) -> None:
        unlocked = self._runner.run(
            f"cryptsetup open --key-file - {partition} {OLD_MAPPER_NAME}",
            input_data=self._config.credentials.source_luks_password,
            raise_on_nonzero_exit=False,
        )
        if not unlocked.success:
            raise MigrationError(
                f"Could not unlock {partition} with the old LUKS password: {unlocked.stderr.strip()}"
            )

        # subvolid=5 is the btrfs top level, where every subvolume is visible
        self._runner.run(f"mkdir -p {OLD_MOUNT_DIRECTORY}")
        mounted = self._runner.run(
            f"mount -t btrfs -o subvolid=5 /dev/mapper/{OLD_MAPPER_NAME} {OLD_MOUNT_DIRECTORY}",
            raise_on_nonzero_exit=False,
        )
        if not mounted.success:
            self._runner.run(f"cryptsetup close {OLD_MAPPER_NAME}", raise_on_nonzero_exit=False)
            raise MigrationError(f"Could not mount the existing system: {mounted.stderr.strip()}")

    def _unmount_and_close(self) -> None:
        self._runner.run(f"umount {OLD_MOUNT_DIRECTORY}", raise_on_nonzero_exit=False)
        self._runner.run(f"cryptsetup close {OLD_MAPPER_NAME}", raise_on_nonzero_exit=False)
        self._runner.run(f"rmdir {OLD_MOUNT_DIRECTORY}", raise_on_nonzero_exit=False)

    def _inspect_mounted_system(self, disk: str, root_partition: str) -> ExistingInstallInfo:
        subvolumes = self._runner.run(
            f"btrfs subvolume list {OLD_MOUNT_DIRECTORY}", raise_on_nonzero_exit=False
        ).stdout
        home_subvolume = "@home" if "@home" in subvolumes else ""
        return ExistingInstallInfo(
            disk=disk,
            root_partition=root_partition,
            home_subvolume=home_subvolume,
            home_size_mb=self._size_mb(f"{OLD_MOUNT_DIRECTORY}/{home_subvolume}")
            if home_subvolume
            else 0,
            secure_boot_directory=self._find_secure_boot_directory(),
        )

    def _find_secure_boot_directory(self) -> str:
        for sbctl_directory in (SBCTL_DIRECTORY, SBCTL_LEGACY_DIRECTORY):
            candidate = path_in_old_root(sbctl_directory)
            if directory_exists(self._runner, f"{candidate}/keys"):
                return candidate
        return ""

    def _ensure_staging_space(self, existing: ExistingInstallInfo) -> None:
        migration = self._config.migration
        required_mb = STAGING_HEADROOM_MB
        if migration.preserve_home:
            required_mb += existing.home_size_mb
        for path in migration.additional_paths:
            required_mb += self._size_mb(path_in_old_root(path))

        staging_parent = str(Path(STAGING_DIRECTORY).parent)
        df_output = self._runner.run(
            f"df --output=avail -m {staging_parent}", raise_on_nonzero_exit=False
        )
        available_field = df_output.stdout.strip().split("\n")[-1].strip()
        available_mb = int(available_field) if available_field.isdigit() else 0
        logger.info(
            "Staging needs ~%sMB, %s has %sMB free", required_mb, staging_parent, available_mb
        )
        if available_mb < required_mb:
            raise MigrationError(
                f"Not enough space to stage the migration in {staging_parent}: "
                f"need ~{required_mb}MB, {available_mb}MB available"
            )

    def _copy_to_staging(self, existing: ExistingInstallInfo) -> None:
        migration = self._config.migration
        self._runner.run(f"rm -rf {STAGING_DIRECTORY}")
        self._runner.run(f"mkdir -p {STAGING_DIRECTORY}")

        if migration.preserve_home and existing.home_subvolume:
            self._copy_into_staging(f"{OLD_MOUNT_DIRECTORY}/{existing.home_subvolume}", "home")
        if migration.preserve_secure_boot_keys and existing.secure_boot_directory:
            self._copy_into_staging(existing.secure_boot_directory, "sbctl")
        for path in migration.additional_paths:
            source = path_in_old_root(path)
            if path_exists(self._runner, source):
                destination = f"{STAGING_DIRECTORY}/additional/{path.lstrip('/')}"
                self._runner.run(f"mkdir -p {Path(destination).parent}")
                self._runner.run(f"cp -a {source} {destination}")

    def _copy_into_staging(self, source_directory: str, staging_name: str) -> None:
        logger.info("Staging %s...", source_directory)
        self._runner.run(f"mkdir -p {STAGING_DIRECTORY}/{staging_name}")
        self._runner.run(f"cp -a {source_directory}/. {STAGING_DIRECTORY}/{staging_name}/")

    def _size_mb(self, path: str) -> int:
        du_output = self._runner.run(f"du -sm {path}", raise_on_nonzero_exit=False).stdout.split()
        return int(du_output[0]) if du_output and du_output[0].isdigit() else 0


class MigrationRestoreStepExecutor(StepExecutor):
    def execute(self) -> None:
        if not directory_exists(self._runner, STAGING_DIRECTORY):
            logger.info("No staging data to restore")
            return

        staged_destinations = (
            (f"{STAGING_DIRECTORY}/home", f"{TARGET_ROOT}/home"),
            (f"{STAGING_DIRECTORY}/sbctl", f"{TARGET_ROOT}{SBCTL_DIRECTORY}"),
            (f"{STAGING_DIRECTORY}/additional", TARGET_ROOT),
        )
        for staged_directory, destination in staged_destinations:
            if directory_exists(self._runner, staged_directory):
                logger.info("Restoring %s...", destination)
                self._runner.run(f"mkdir -p {destination}")
                self._runner.run(f"cp -a {staged_directory}/. {destination}/")

        self._report_restored_data()
        self._runner.run(f"rm -rf {STAGING_DIRECTORY}")

    def _report_restored_data(self) -> None:
        migration = self._config.migration
        if migration.preserve_home:
            home_listing = self._runner.run(
                f"ls -A {TARGET_ROOT}/home", raise_on_nonzero_exit=False
            ).stdout
            if not home_listing.strip():
                logger.warning("Restored home directory is empty")
        if migration.preserve_secure_boot_keys and not directory_exists(
            self._runner, f"{TARGET_ROOT}{SBCTL_DIRECTORY}/keys"
        ):
            logger.warning("Secure boot keys were not restored")
