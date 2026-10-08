"""USB boot drive: preparing the drive, the safeguards on the installed system, and
cloning a spare.
"""

import logging
import math
import time

from arch_installer.config.models import CRYPTROOT_MAPPER_NAME, WipeMethod, derive_partition_path
from arch_installer.core.command import CommandRunner
from arch_installer.errors import UsbBootDriveError
from arch_installer.executors.base import (
    TARGET_ROOT,
    StepExecutor,
    is_mountpoint,
    partition_uuid,
    path_exists,
    stable_disk_path,
    write_file,
)

logger = logging.getLogger(__name__)

MEBIBYTE = 1024 * 1024
# a LUKS2 header with its default keyslot area takes 16 MiB
LUKS_HEADER_PARTITION_SIZE_MB = 32
# GPT structures and partition alignment at both ends of the drive
PARTITION_TABLE_SIZE_MB = 2

RECOVERY_MOUNT = "/run/dali/usb-recovery"
ARCHISO_DIRECTORY = "arch"
ARCHISO_KERNEL = "arch/boot/x86_64/vmlinuz-linux"
ARCHISO_INITRAMFS = "arch/boot/x86_64/initramfs-linux.img"
ARCHISO_ROOT_IMAGE = "arch/x86_64/airootfs.sfs"
ARCHISO_VERSION_FILE = "arch/version"
# ISO 9660 primary volume descriptor: its signature, and the image size as a 32-bit
# little-endian block count and a 16-bit little-endian block size
ISO9660_SIGNATURE = "CD001"
ISO9660_SIGNATURE_OFFSET = 32769
ISO9660_BLOCK_COUNT_OFFSET = 32848
ISO9660_BLOCK_SIZE_OFFSET = 32896

USB_BOOT_SETTINGS_FILE = "/etc/default/usb-boot-drive"
USB_BOOT_GUARD_SCRIPT = "/usr/local/bin/check-usb-boot-drive"
USB_BOOT_GUARD_HOOK = "/etc/pacman.d/hooks/00-usb-boot-drive.hook"
SNAPSHOT_CATCH_UP_SERVICE = "snapshot-ukis-catch-up.service"
SNAPSHOT_CATCH_UP_RULE = "/etc/udev/rules.d/90-usb-boot-drive.rules"
# manage-snapshot-ukis leaves this marker when the drive was away during a refresh
SNAPSHOT_REFRESH_PENDING_MARKER = "/var/lib/manage-snapshot-ukis/refresh-pending"
SNAPSHOT_MANAGER = "/usr/local/bin/manage-snapshot-ukis"

# mounted on access and unmounted when idle, so the drive can be pulled at any time
# without a dirty FAT, and plugged back in without a remount
REMOVABLE_EFI_MOUNT_OPTIONS = (
    "umask=0077,noauto,nofail,x-systemd.automount,"
    "x-systemd.idle-timeout=60s,x-systemd.device-timeout=5s"
)

# the union of what mkinitcpio, sbctl and systemd-boot react to: every one of these
# rewrites a file on the drive's EFI partition
BOOT_FILE_TARGETS = (
    "boot/*",
    "efi/*",
    "usr/lib/initcpio/*",
    "usr/lib/firmware/*",
    "usr/lib/modules/*/vmlinuz",
    "usr/lib/modules/*/extramodules/*",
    "usr/lib/modprobe.d/",
    "usr/src/*/dkms.conf",
    "usr/lib/systemd/systemd",
    "usr/lib/systemd/systemd-udevd",
    "usr/lib/**/efi/*.efi*",
    "usr/share/**/*.efi*",
    "usr/bin/cryptsetup",
    "usr/bin/lvm",
    "usr/bin/mdadm",
    "usr/bin/pdata_tools",
    "usr/lib/libcryptsetup.so",
    "usr/lib/libp11-kit.so",
    "usr/lib/libpcsclite.so",
)


def removable_efi_fstab(fstab: str, efi_partition_uuid: str) -> str:
    entry = f"PARTUUID={efi_partition_uuid}\t/efi\tvfat\t{REMOVABLE_EFI_MOUNT_OPTIONS}\t0 2"
    kept_lines = [line for line in fstab.splitlines() if line.split()[1:2] != ["/efi"]]
    return "\n".join([*kept_lines, entry]) + "\n"


def usb_boot_settings(efi_partition_uuid: str) -> str:
    return f"""# the USB boot drive this system starts from, written by the DALI installer
EFI_PARTITION_UUID={efi_partition_uuid}
"""


USB_BOOT_GUARD_SCRIPT_CONTENT = f"""#!/usr/bin/env bash
# run by pacman before a transaction that rewrites boot files: they live on the USB boot
# drive, and a kernel update without it would leave the drive's UKIs behind the modules
set -uo pipefail

. {USB_BOOT_SETTINGS_FILE}

# listing /efi mounts the drive (automount) when it is plugged in
ls /efi >/dev/null 2>&1
mounted_uuid=$(findmnt -n -t vfat -o PARTUUID --mountpoint /efi 2>/dev/null)
if [ "$mounted_uuid" != "$EFI_PARTITION_UUID" ]; then
    echo "The USB boot drive is not plugged in. This transaction rewrites boot files on" >&2
    echo "its EFI partition (PARTUUID $EFI_PARTITION_UUID): plug it in and run it again." >&2
    exit 1
fi
"""


def usb_boot_guard_hook() -> str:
    targets = "\n".join(f"Target = {target}" for target in BOOT_FILE_TARGETS)
    return f"""# refuses transactions that rewrite boot files while the USB boot drive is unplugged
# sorts before every other hook, so nothing (snap-pac included) runs before the check

[Trigger]
Type = Path
Operation = Install
Operation = Upgrade
Operation = Remove
{targets}

[Trigger]
Type = Package
Operation = Install
Operation = Upgrade
Target = mkinitcpio

[Action]
Description = Checking that the USB boot drive is plugged in...
When = PreTransaction
Exec = {USB_BOOT_GUARD_SCRIPT}
AbortOnFail
"""


def snapshot_catch_up_rule(efi_partition_uuid: str) -> str:
    return f"""# builds the snapshot UKIs skipped while the USB boot drive was unplugged
ACTION=="add", SUBSYSTEM=="block", ENV{{ID_PART_ENTRY_UUID}}=="{efi_partition_uuid}", TAG+="systemd", ENV{{SYSTEMD_WANTS}}+="{SNAPSHOT_CATCH_UP_SERVICE}"
"""


SNAPSHOT_CATCH_UP_SERVICE_CONTENT = f"""[Unit]
Description=Build the snapshot UKIs skipped while the USB boot drive was unplugged
ConditionPathExists={SNAPSHOT_REFRESH_PENDING_MARKER}

[Service]
Type=oneshot
ExecStart={SNAPSHOT_MANAGER} refresh
"""


def recovery_partition_size_mb(iso_size_bytes: int) -> int:
    # ext4 metadata and journal take a few percent of the partition
    iso_size_mb = math.ceil(iso_size_bytes / MEBIBYTE)
    return iso_size_mb + iso_size_mb // 10 + 64


class UsbDriveInspector:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def require_disk(self, device: str) -> None:
        result = self._runner.run(f"lsblk -dno TYPE {device}", raise_on_nonzero_exit=False)
        if not result.success or result.stdout.strip() != "disk":
            raise UsbBootDriveError(f"{device} is not a disk")
        disk_name = device.rsplit("/", 1)[-1]
        removable = self._runner.run(
            f"cat /sys/block/{disk_name}/removable", raise_on_nonzero_exit=False
        )
        if removable.success and removable.stdout.strip() == "0":
            # virtio disks in a VM report non-removable, so this is a warning not a failure
            logger.warning("%s reports as non-removable, make sure it is the USB drive", device)

    def size_bytes(self, device: str) -> int:
        return int(self._runner.run(f"blockdev --getsize64 {device}").stdout.strip())

    def wait_for_partition(self, device: str, partition: str) -> None:
        for _ in range(15):
            if path_exists(self._runner, partition):
                return
            time.sleep(1)
            self._runner.run(f"partprobe {device}", raise_on_nonzero_exit=False)
        raise UsbBootDriveError(f"{partition} did not appear on {device}")

    def holds_luks_header(self, partition: str) -> bool:
        return self._runner.run(
            f"cryptsetup isLuks {partition}", raise_on_nonzero_exit=False
        ).success


class ArchIsoImage:
    # a file, or a block device holding the image such as the live medium (/dev/sr0)
    def __init__(self, runner: CommandRunner, source: str) -> None:
        self._runner = runner
        self.source = source

    def size_bytes(self) -> int:
        if self._read_text(ISO9660_SIGNATURE_OFFSET, len(ISO9660_SIGNATURE)) != ISO9660_SIGNATURE:
            raise UsbBootDriveError(f"{self.source} is not an ISO 9660 image")
        listing = self._runner.run(
            f"bsdtar -tf {self.source} {ARCHISO_KERNEL} {ARCHISO_INITRAMFS} {ARCHISO_ROOT_IMAGE}",
            raise_on_nonzero_exit=False,
        )
        if not listing.success:
            raise UsbBootDriveError(
                f"{self.source} is not an Arch Linux ISO "
                f"({ARCHISO_KERNEL}, {ARCHISO_INITRAMFS}, {ARCHISO_ROOT_IMAGE})"
            )
        block_count = self._read_number(ISO9660_BLOCK_COUNT_OFFSET, 4)
        block_size = self._read_number(ISO9660_BLOCK_SIZE_OFFSET, 2)
        return block_count * block_size

    def extract_live_system(self, directory: str) -> None:
        self._runner.run(f"bsdtar -xf {self.source} -C {directory} {ARCHISO_DIRECTORY}")
        self._runner.run(f"sync -f {directory}")

    def _read(self, offset: int, length: int, format_command: str) -> str:
        return self._runner.run(
            f"dd if={self.source} bs=4096 skip={offset} count={length} "
            f"iflag=skip_bytes,count_bytes status=none | {format_command}"
        ).stdout.strip()

    def _read_text(self, offset: int, length: int) -> str:
        return self._read(offset, length, "cat")

    def _read_number(self, offset: int, length: int) -> int:
        return int(self._read(offset, length, f"od -An -tu{length}"))


class UsbBootDriveStepExecutor(StepExecutor):
    def execute(self) -> None:
        usb_boot = self._config.usb_boot
        inspector = UsbDriveInspector(self._runner)
        if usb_boot.device == self._config.storage.target_disk:
            raise UsbBootDriveError(f"{usb_boot.device} is the target disk, not a USB drive")
        inspector.require_disk(usb_boot.device)
        target_disk = self._config.storage.target_disk
        if not stable_disk_path(self._runner, target_disk):
            raise UsbBootDriveError(
                f"{target_disk} has no /dev/disk/by-id name. Without a partition table the "
                "boot finds the disk only by that name"
            )
        if self._keeps_existing_drive(inspector):
            return

        recovery_iso = ArchIsoImage(self._runner, usb_boot.iso_path)
        iso_size_bytes = recovery_iso.size_bytes() if usb_boot.recovery_system else 0
        recovery_size_mb = recovery_partition_size_mb(iso_size_bytes) if iso_size_bytes else 0
        self._require_capacity(inspector, recovery_size_mb)

        logger.info("Preparing the USB boot drive %s...", usb_boot.device)
        self._release_drive()
        self._partition(inspector, recovery_size_mb)
        self._runner.run(f"mkfs.vfat -F32 -n EFI {usb_boot.efi_partition}")
        if usb_boot.recovery_system:
            self._store_recovery_system(recovery_iso)

    def _keeps_existing_drive(self, inspector: UsbDriveInspector) -> bool:
        # a resumed installation keeps the storage it finds, and that storage can only be
        # unlocked with the header already on the drive
        resuming = is_mountpoint(self._runner, TARGET_ROOT) or (
            self._config.storage.wipe_method == WipeMethod.SKIP
            and not self._config.migration.enabled
        )
        header_partition = self._config.usb_boot.luks_header_partition
        if resuming and inspector.holds_luks_header(header_partition):
            logger.info("Keeping the USB boot drive, %s holds the LUKS header", header_partition)
            return True
        return False

    def _require_capacity(self, inspector: UsbDriveInspector, recovery_size_mb: int) -> None:
        device = self._config.usb_boot.device
        needed_mb = (
            PARTITION_TABLE_SIZE_MB
            + self._config.storage.efi_size_mb
            + LUKS_HEADER_PARTITION_SIZE_MB
            + recovery_size_mb
        )
        available_mb = inspector.size_bytes(device) // MEBIBYTE
        if needed_mb > available_mb:
            raise UsbBootDriveError(
                f"{device} holds {available_mb} MiB, the USB boot drive needs {needed_mb} MiB"
            )

    def _release_drive(self) -> None:
        # a previous attempt may still use the drive: the open volume holds its header
        usb_boot = self._config.usb_boot
        self._runner.run(f"umount -R {TARGET_ROOT}", raise_on_nonzero_exit=False)
        self._runner.run(f"umount {RECOVERY_MOUNT}", raise_on_nonzero_exit=False)
        self._runner.run("swapoff -a", raise_on_nonzero_exit=False)
        self._runner.run(f"cryptsetup close {CRYPTROOT_MAPPER_NAME}", raise_on_nonzero_exit=False)
        for partition in (usb_boot.efi_partition, usb_boot.recovery_partition):
            self._runner.run(f"umount {partition}", raise_on_nonzero_exit=False)

    def _partition(self, inspector: UsbDriveInspector, recovery_size_mb: int) -> None:
        usb_boot = self._config.usb_boot
        device = usb_boot.device
        self._runner.run(f"wipefs -af {device}", raise_on_nonzero_exit=False)
        self._runner.run(f"sgdisk -Z {device}")
        self._runner.run(f"sgdisk -n1:0:+{self._config.storage.efi_size_mb}M -t1:ef00 {device}")
        self._runner.run(f"sgdisk -n2:0:+{LUKS_HEADER_PARTITION_SIZE_MB}M -t2:8300 {device}")
        last_partition = usb_boot.luks_header_partition
        if recovery_size_mb:
            self._runner.run(f"sgdisk -n3:0:+{recovery_size_mb}M -t3:8300 {device}")
            last_partition = usb_boot.recovery_partition
        self._runner.run(f"partprobe {device}", raise_on_nonzero_exit=False)
        self._runner.run("udevadm settle", raise_on_nonzero_exit=False)
        inspector.wait_for_partition(device, last_partition)
        # an old header must not linger until the storage step formats a new one
        self._runner.run(
            f"wipefs -af {usb_boot.luks_header_partition}", raise_on_nonzero_exit=False
        )

    def _store_recovery_system(self, recovery_iso: ArchIsoImage) -> None:
        recovery_partition = self._config.usb_boot.recovery_partition
        logger.info(
            "Copying the live system of %s to the recovery partition...", recovery_iso.source
        )
        self._runner.run(f"mkfs.ext4 -F -m 0 -L RECOVERY {recovery_partition}")
        self._runner.run(f"mkdir -p {RECOVERY_MOUNT}")
        self._runner.run(f"mount {recovery_partition} {RECOVERY_MOUNT}")
        try:
            recovery_iso.extract_live_system(RECOVERY_MOUNT)
        finally:
            self._runner.run(f"umount {RECOVERY_MOUNT}", raise_on_nonzero_exit=False)


class UsbBootSafeguardsStepExecutor(StepExecutor):
    def execute(self) -> None:
        efi_partition_uuid = partition_uuid(self._runner, self._config.usb_boot.efi_partition)
        logger.info("Installing the USB boot drive safeguards...")
        self._mount_efi_on_demand(efi_partition_uuid)
        self._install_update_guard(efi_partition_uuid)
        if self._config.boot.enable_snapshot_boot:
            self._install_snapshot_catch_up(efi_partition_uuid)

    def _mount_efi_on_demand(self, efi_partition_uuid: str) -> None:
        fstab_path = f"{TARGET_ROOT}/etc/fstab"
        fstab = self._runner.run(f"cat {fstab_path}").stdout
        write_file(self._runner, fstab_path, removable_efi_fstab(fstab, efi_partition_uuid))

    def _install_update_guard(self, efi_partition_uuid: str) -> None:
        write_file(
            self._runner,
            f"{TARGET_ROOT}{USB_BOOT_SETTINGS_FILE}",
            usb_boot_settings(efi_partition_uuid),
        )
        guard_script = f"{TARGET_ROOT}{USB_BOOT_GUARD_SCRIPT}"
        self._runner.run(f"mkdir -p {TARGET_ROOT}/etc/pacman.d/hooks {TARGET_ROOT}/usr/local/bin")
        write_file(self._runner, guard_script, USB_BOOT_GUARD_SCRIPT_CONTENT)
        self._runner.run(f"chmod 755 {guard_script}")
        write_file(self._runner, f"{TARGET_ROOT}{USB_BOOT_GUARD_HOOK}", usb_boot_guard_hook())

    def _install_snapshot_catch_up(self, efi_partition_uuid: str) -> None:
        self._runner.run(f"mkdir -p {TARGET_ROOT}/etc/udev/rules.d")
        write_file(
            self._runner,
            f"{TARGET_ROOT}{SNAPSHOT_CATCH_UP_RULE}",
            snapshot_catch_up_rule(efi_partition_uuid),
        )
        write_file(
            self._runner,
            f"{TARGET_ROOT}/etc/systemd/system/{SNAPSHOT_CATCH_UP_SERVICE}",
            SNAPSHOT_CATCH_UP_SERVICE_CONTENT,
        )


# a spare drive with the same partitions, partition UUIDs and contents, so it boots the
# system and unlocks it on its own. a drive's loss is otherwise the loss of the header
class UsbBootDriveCloner:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner
        self._inspector = UsbDriveInspector(runner)

    def clone(self, source_device: str, spare_device: str) -> None:
        if source_device == spare_device:
            raise UsbBootDriveError("The spare drive must be another drive than the USB boot drive")
        self._inspector.require_disk(source_device)
        self._inspector.require_disk(spare_device)
        source_partitions = self._existing_partitions(source_device)
        if len(source_partitions) < 2 or not self._inspector.holds_luks_header(
            source_partitions[1]
        ):
            raise UsbBootDriveError(f"{source_device} is not a USB boot drive (no LUKS header)")
        mountpoints = self._runner.run(f"lsblk -nro MOUNTPOINT {spare_device}").stdout
        if mountpoints.strip():
            raise UsbBootDriveError(f"{spare_device} has mounted partitions")

        logger.info("Cloning %s to %s...", source_device, spare_device)
        self._runner.run(f"wipefs -af {spare_device}", raise_on_nonzero_exit=False)
        # copies the partition table with its partition UUIDs, which the system boots by
        self._runner.run(f"sgdisk --replicate={spare_device} {source_device}")
        self._runner.run(f"sgdisk -e {spare_device}", raise_on_nonzero_exit=False)
        self._runner.run(f"partprobe {spare_device}", raise_on_nonzero_exit=False)
        self._runner.run("udevadm settle", raise_on_nonzero_exit=False)

        spare_partitions = [
            derive_partition_path(spare_device, number)
            for number in range(1, len(source_partitions) + 1)
        ]
        for source_partition, spare_partition in zip(
            source_partitions, spare_partitions, strict=True
        ):
            self._inspector.wait_for_partition(spare_device, spare_partition)
            self._runner.run(
                f"dd if={source_partition} of={spare_partition} bs=4M conv=fsync status=none"
            )
        self._runner.run(f"cmp {source_partitions[1]} {spare_partitions[1]}")
        logger.info(
            "%s is a spare USB boot drive. Keep it apart from the original: both carry the "
            "same partition UUIDs",
            spare_device,
        )

    def _existing_partitions(self, device: str) -> list[str]:
        # the EFI and header partitions, then the recovery partition when there is one
        partitions = [derive_partition_path(device, number) for number in (1, 2, 3)]
        return [partition for partition in partitions if path_exists(self._runner, partition)]
