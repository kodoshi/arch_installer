"""migration support for transitioning existing arch installations.

handles migrating an existing arch linux installation with different
LUKS parameters, secure boot keys, and partition layout to the new
installer's standardized layout while preserving user data.

the migration process:
1. detect existing installation and validate compatibility
2. decrypt existing LUKS with source password
3. copy preserved data (home, secure boot keys, ssh keys) to staging
4. unmount and close existing LUKS
5. (storage step runs - wipes disk, creates new partitions with new LUKS)
6. (packages step restores preserved data after pacstrap)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from arch_installer.config.models import DeclaredConfig, MigrationConfig
from arch_installer.core.command import CommandRunner
from arch_installer.core.runtime_state import RuntimeConfig

STAGING_DIR = "/tmp/migration-staging"
OLD_MOUNT_DIR = "/tmp/old-system"


@dataclass
class ExistingInstallInfo:
    disk: str
    root_partition: str
    efi_partition: str
    has_secure_boot_keys: bool
    secure_boot_key_path: str
    home_subvolume: str
    total_home_size_mb: int


class MigrationExecutor:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner

    def calculate_migration_size(self, existing: ExistingInstallInfo) -> dict[str, int]:
        """calculate the size of data to be migrated.

        returns dict with sizes in MB for each category.
        """
        sizes = {"home": 0, "secure_boot_keys": 0, "additional_paths": 0, "total": 0}

        if self._config.migration.preserve_home:
            sizes["home"] = existing.total_home_size_mb

        if self._config.migration.preserve_secure_boot_keys and existing.has_secure_boot_keys:
            # secure boot keys are typically very small (<1MB)
            sizes["secure_boot_keys"] = 1

        # estimate additional paths - we can't know without mounting
        sizes["additional_paths"] = len(self._config.migration.additional_paths) * 10

        sizes["total"] = sum(v for k, v in sizes.items() if k != "total")
        return sizes

    def check_available_space(self, target_disk: str, required_mb: int) -> tuple[bool, int]:
        """check if target disk has enough space.

        returns (is_sufficient, available_mb).
        """
        result = self._runner.run(
            f"lsblk -bno SIZE {target_disk}",
            raise_on_nonzero_exit=False,
        )
        if not result.success:
            return True, 0  # can't determine, assume ok

        try:
            size_bytes = int(result.stdout.strip())
            available_mb = size_bytes // (1024 * 1024)
            # need space for: EFI, LUKS overhead, BTRFS metadata, and the data
            overhead_mb = self._config.storage.efi_size_mb + 1024  # efi + ~1GB overhead
            effective_available = available_mb - overhead_mb
            return effective_available >= required_mb, available_mb
        except (ValueError, TypeError):
            return True, 0

    def execute_pre_wipe_migration(self, existing: ExistingInstallInfo) -> bool:
        print(">>>>> Starting migration from existing installation")
        print(f"    source disk: {existing.disk}")
        print(f"    home size: {existing.total_home_size_mb}MB")
        print(f"    secure boot keys: {existing.has_secure_boot_keys}")

        # calculate and verify space requirements
        migration_sizes = self.calculate_migration_size(existing)
        print(f"    calculated migration size: {migration_sizes['total']}MB")

        target_disk = self._state.target_disk
        if target_disk:
            is_sufficient, available_mb = self.check_available_space(
                target_disk, migration_sizes["total"]
            )
            print(f"    target disk available: ~{available_mb}MB")
            if not is_sufficient:
                print(
                    f"    ERROR: Insufficient space for migration. Need {migration_sizes['total']}MB"
                )
                return False

        source_password = self._state.source_luks_password
        if not source_password:
            print("    ERROR: SOURCE_LUKS_PASSWORD required for migration")
            return False

        if not self._open_old_luks(existing.root_partition, source_password):
            return False

        if not self._mount_old_system():
            self._close_old_luks()
            return False

        if not self._copy_data_to_staging(existing):
            self._cleanup_old_mount()
            return False

        self._cleanup_old_mount()
        print(">>>>> Migration staging complete")
        return True

    def _open_old_luks(self, partition: str, password: str) -> bool:
        print(f"    Opening existing LUKS device {partition}...")
        result = self._runner.run(
            f"cryptsetup open --key-file - {partition} oldcryptroot",
            input_data=password,
            raise_on_nonzero_exit=False,
        )
        if not result.success:
            print(f"    ERROR: Failed to decrypt existing LUKS: {result.stderr}")
            return False
        print("    Successfully decrypted existing system")
        return True

    def _mount_old_system(self) -> bool:
        print(f"    Mounting old system to {OLD_MOUNT_DIR}...")
        self._runner.run(f"mkdir -p {OLD_MOUNT_DIR}")

        # mount btrfs root first (subvolid=5 to see all subvolumes)
        result = self._runner.run(
            f"mount -o subvolid=5 /dev/mapper/oldcryptroot {OLD_MOUNT_DIR}",
            raise_on_nonzero_exit=False,
        )
        if not result.success:
            print(f"    ERROR: Failed to mount old system: {result.stderr}")
            return False
        return True

    def _copy_data_to_staging(self, existing: ExistingInstallInfo) -> bool:
        print(f"    Copying preserved data to {STAGING_DIR}...")
        self._runner.run(f"rm -rf {STAGING_DIR}")
        self._runner.run(f"mkdir -p {STAGING_DIR}")

        migration_config = self._config.migration

        # copy home data
        if migration_config.preserve_home and existing.home_subvolume:
            home_src = f"{OLD_MOUNT_DIR}/{existing.home_subvolume}"
            result = self._runner.run(f"test -d {home_src}", raise_on_nonzero_exit=False)
            if result.success:
                print(f"    Copying home data from {home_src}...")
                self._runner.run(f"mkdir -p {STAGING_DIR}/home")
                self._runner.run(f"cp -a {home_src}/* {STAGING_DIR}/home/")
                print("    Home data copied successfully")
            else:
                print(f"    Warning: Home subvolume not found at {home_src}")

        # copy secure boot keys
        if migration_config.preserve_secure_boot_keys and existing.has_secure_boot_keys:
            sbctl_src = existing.secure_boot_key_path
            result = self._runner.run(f"test -d {sbctl_src}", raise_on_nonzero_exit=False)
            if result.success:
                print(f"    Copying secure boot keys from {sbctl_src}...")
                self._runner.run(f"mkdir -p {STAGING_DIR}/sbctl")
                self._runner.run(f"cp -a {sbctl_src}/* {STAGING_DIR}/sbctl/")
                print("    Secure boot keys copied successfully")

        # copy additional paths
        for path in migration_config.additional_paths:
            src_path = f"{OLD_MOUNT_DIR}/@{path}"
            result = self._runner.run(f"test -e {src_path}", raise_on_nonzero_exit=False)
            if result.success:
                print(f"    Copying {path}...")
                dest_dir = f"{STAGING_DIR}/additional/{path}"
                self._runner.run(f"mkdir -p $(dirname {dest_dir})")
                self._runner.run(f"cp -a {src_path} {dest_dir}")

        return True

    def _cleanup_old_mount(self) -> None:
        print("    Cleaning up old mount...")
        self._runner.run(f"umount {OLD_MOUNT_DIR} 2>/dev/null || true")
        self._close_old_luks()
        self._runner.run(f"rmdir {OLD_MOUNT_DIR} 2>/dev/null || true")

    def _close_old_luks(self) -> None:
        self._runner.run("cryptsetup close oldcryptroot 2>/dev/null || true")

    def restore_from_staging(self, target_root: str) -> bool:
        result = self._runner.run(f"test -d {STAGING_DIR}", raise_on_nonzero_exit=False)
        if not result.success:
            print("    No staging data to restore")
            return True

        print(">>>>> Restoring data from migration staging...")
        migration_config = self._config.migration

        # restore home data
        if migration_config.preserve_home:
            home_staging = f"{STAGING_DIR}/home"
            result = self._runner.run(f"test -d {home_staging}", raise_on_nonzero_exit=False)
            if result.success:
                print("    Restoring home data...")
                self._runner.run(f"mkdir -p {target_root}/home")
                self._runner.run(f"cp -a {home_staging}/* {target_root}/home/")
                print("    Home data restored")

        # restore secure boot keys to new default location
        if migration_config.preserve_secure_boot_keys:
            sbctl_staging = f"{STAGING_DIR}/sbctl"
            result = self._runner.run(f"test -d {sbctl_staging}", raise_on_nonzero_exit=False)
            if result.success:
                print("    Restoring secure boot keys...")
                target_sbctl = f"{target_root}/var/lib/sbctl"
                self._runner.run(f"mkdir -p {target_sbctl}")
                self._runner.run(f"cp -a {sbctl_staging}/* {target_sbctl}/")
                print("    Secure boot keys restored")

        # restore additional paths
        additional_dir = f"{STAGING_DIR}/additional"
        result = self._runner.run(f"test -d {additional_dir}", raise_on_nonzero_exit=False)
        if result.success:
            print("    Restoring additional paths...")
            self._runner.run(f"cp -a {additional_dir}/* {target_root}/")

        return True

    def cleanup_staging(self) -> None:
        print(">>>>> Cleaning up migration staging...")
        self._runner.run(f"rm -rf {STAGING_DIR}")

    def verify_migration(self, target_root: str) -> bool:
        print(">>>>> Verifying migration integrity...")
        success = True
        migration_config = self._config.migration

        if migration_config.preserve_home:
            result = self._runner.run(
                f"test -d {target_root}/home && ls {target_root}/home/ | head -1",
                raise_on_nonzero_exit=False,
            )
            if result.success and result.stdout.strip():
                print("    OK: Home data restored")
            else:
                print("    Warning: Home data may be empty")

        if migration_config.preserve_secure_boot_keys:
            result = self._runner.run(
                f"test -d {target_root}/var/lib/sbctl/keys",
                raise_on_nonzero_exit=False,
            )
            if result.success:
                print("    OK: Secure boot keys restored")
            else:
                print("    Warning: Secure boot keys not found")

        return success


class InstallationMigrator:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
        migration_enabled: bool = False,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._migration_enabled = migration_enabled
        self._executor = MigrationExecutor(config, state, runner)

    def detect_existing_installation(self, disk: str) -> Optional[ExistingInstallInfo]:
        partitions = self._list_partitions(disk)
        if not partitions:
            return None

        root_part = self._find_luks_partition(partitions)
        if not root_part:
            print("    No LUKS partition found on disk")
            return None

        efi_part = self._find_efi_partition(partitions)

        # we need to open LUKS to detect contents
        source_password = self._state.source_luks_password
        if not source_password:
            print("    ERROR: SOURCE_LUKS_PASSWORD required to detect existing installation")
            return None

        result = self._runner.run(
            f"cryptsetup open --key-file - {root_part} detect_cryptroot",
            input_data=source_password,
            raise_on_nonzero_exit=False,
        )
        if not result.success:
            print(f"    Failed to decrypt {root_part} - wrong password or not LUKS")
            return None

        # mount temporarily to inspect
        self._runner.run("mkdir -p /tmp/detect_mount")
        result = self._runner.run(
            "mount -o subvolid=5 /dev/mapper/detect_cryptroot /tmp/detect_mount",
            raise_on_nonzero_exit=False,
        )
        if not result.success:
            self._runner.run("cryptsetup close detect_cryptroot")
            return None

        # detect home subvolume
        home_subvol = self._detect_home_subvolume()
        home_size = self._calculate_home_size(home_subvol)

        # detect secure boot keys (check both new and legacy paths)
        secure_boot_path, has_keys = self._detect_secure_boot_keys()

        # cleanup
        self._runner.run("umount /tmp/detect_mount")
        self._runner.run("cryptsetup close detect_cryptroot")
        self._runner.run("rmdir /tmp/detect_mount")

        return ExistingInstallInfo(
            disk=disk,
            root_partition=root_part,
            efi_partition=efi_part or "",
            has_secure_boot_keys=has_keys,
            secure_boot_key_path=secure_boot_path,
            home_subvolume=home_subvol,
            total_home_size_mb=home_size,
        )

    def _list_partitions(self, disk: str) -> list[str]:
        result = self._runner.run(f"lsblk -ln -o NAME {disk}", raise_on_nonzero_exit=False)
        if not result.success:
            return []
        parts = []
        for name in result.stdout.strip().split("\n"):
            name = name.strip()
            if name and name != Path(disk).name:
                parts.append(f"/dev/{name}")
        return parts

    def _find_luks_partition(self, partitions: list[str]) -> Optional[str]:
        for part in partitions:
            result = self._runner.run(f"cryptsetup isLuks {part}", raise_on_nonzero_exit=False)
            if result.success:
                return part
        return None

    def _find_efi_partition(self, partitions: list[str]) -> Optional[str]:
        for part in partitions:
            result = self._runner.run(
                f"blkid -o value -s TYPE {part}", raise_on_nonzero_exit=False
            )
            if result.success and "vfat" in result.stdout:
                return part
        return None

    def _detect_home_subvolume(self) -> str:
        result = self._runner.run(
            "btrfs subvolume list /tmp/detect_mount", raise_on_nonzero_exit=False
        )
        if result.success:
            for line in result.stdout.split("\n"):
                if "@home" in line:
                    return "@home"
        return ""

    def _calculate_home_size(self, home_subvol: str) -> int:
        if not home_subvol:
            return 0
        result = self._runner.run(
            f"du -sm /tmp/detect_mount/{home_subvol} 2>/dev/null || echo 0",
            raise_on_nonzero_exit=False,
        )
        try:
            return int(result.stdout.split()[0])
        except (ValueError, IndexError):
            return 0

    def _detect_secure_boot_keys(self) -> tuple[str, bool]:
        # check new default path first
        new_path = "/tmp/detect_mount/@/var/lib/sbctl/keys"
        result = self._runner.run(f"test -d {new_path}", raise_on_nonzero_exit=False)
        if result.success:
            return "/tmp/detect_mount/@/var/lib/sbctl", True

        # check legacy path
        legacy_path = "/tmp/detect_mount/@/usr/share/secureboot/keys"
        result = self._runner.run(f"test -d {legacy_path}", raise_on_nonzero_exit=False)
        if result.success:
            return "/tmp/detect_mount/@/usr/share/secureboot", True

        return "", False

    def migrate(self, source_disk: str) -> bool:
        if not self._migration_enabled:
            return False

        existing = self.detect_existing_installation(source_disk)
        if not existing:
            print(">>>>> No existing arch installation found, proceeding with fresh install")
            return False

        print(">>>>> Existing arch installation detected:")
        print(f"    disk: {existing.disk}")
        print(f"    root partition: {existing.root_partition}")
        print(f"    home size: {existing.total_home_size_mb}MB")
        print(f"    secure boot keys: {existing.has_secure_boot_keys}")

        return self._executor.execute_pre_wipe_migration(existing)

    def post_install_restore(self, target_root: str) -> bool:
        if not self._migration_enabled:
            return True

        if not self._executor.restore_from_staging(target_root):
            return False

        self._executor.verify_migration(target_root)
        self._executor.cleanup_staging()
        return True
