"""USB boot drive provisioning for a dual-boot machine whose Arch EFI lives on a
removable drive. The internal disk is left to the other operating system's bootloader
when the USB is absent.

USB drive layout (4 partitions):
  1. EFI System Partition  - systemd-boot, the UKIs and the signed bootloaders
  2. ISO Partition (ext4)  - a live Arch ISO for recovery
  3. LUKS header partition - the detached LUKS header of the internal root partition
  4. Backup partition      - dotfiles, config and the package list (see usb_backup)
"""

import logging
import time

from arch_installer.config.models import InstallerConfig, derive_partition_path
from arch_installer.core.command import CommandRunner
from arch_installer.executors.base import (
    SBCTL_DB_KEY,
    TARGET_EFI,
    TARGET_ROOT,
    Executor,
    file_exists,
    path_exists,
)

logger = logging.getLogger(__name__)

LUKS_HEADER_PARTITION_SIZE_MB = 64
LUKS_HEADER_IMAGE = "luks_header.img"
LUKS_HEADER_STAGING = f"/tmp/{LUKS_HEADER_IMAGE}"

USB_EFI_MOUNT = "/mnt/usb-efi"
USB_ISO_MOUNT = "/mnt/usb-iso"
USB_HEADER_MOUNT = "/mnt/usb-header"
ISO_LOOP_MOUNT = "/mnt/iso-mount"

ISO_RECOVERY_SOURCES = ("/run/archiso/bootmnt/arch.iso", "/run/archiso/cowspace/arch.iso")
SYSTEMD_BOOT_SOURCES = (
    f"{TARGET_ROOT}/usr/lib/systemd/boot/efi/systemd-bootx64.efi",
    "/usr/lib/systemd/boot/efi/systemd-bootx64.efi",
)
USB_BOOTLOADER_BINARIES = (
    f"{USB_EFI_MOUNT}/EFI/systemd/systemd-bootx64.efi",
    f"{USB_EFI_MOUNT}/EFI/BOOT/BOOTX64.EFI",
)

LIVE_ISO_BOOT_ENTRY = """title   Arch Linux Live ISO (Recovery)
efi     /EFI/recovery/archiso.efi
"""


class UsbBootDrive:
    # the mechanics shared by the installer and the standalone usb-init command
    def __init__(self, config: InstallerConfig, runner: CommandRunner) -> None:
        self._config = config
        self._runner = runner

    @property
    def device(self) -> str:
        return self._config.usb_boot.device

    @property
    def efi_partition(self) -> str:
        return derive_partition_path(self.device, 1)

    @property
    def iso_partition(self) -> str:
        return derive_partition_path(self.device, 2)

    @property
    def header_partition(self) -> str:
        return derive_partition_path(self.device, 3)

    @property
    def backup_partition(self) -> str:
        return derive_partition_path(self.device, 4)

    def provision(self) -> None:
        if not self.device:
            raise RuntimeError("No USB device (USB_BOOT_DEVICE or usb_boot.device)")
        self._validate_device(self.device)
        self._partition(self.device)
        self._format()
        self._runner.run(f"mkdir -p {USB_EFI_MOUNT}")
        self._runner.run(f"mount {self.efi_partition} {USB_EFI_MOUNT}")

    def install_bootloader(self) -> None:
        logger.info("Installing systemd-boot on the USB EFI partition...")
        self._runner.run(
            f"bootctl install --esp-path={USB_EFI_MOUNT} --no-variables",
            raise_on_nonzero_exit=False,
        )
        # the random seed ties an ESP to one machine, which a portable drive must not do
        self._runner.run(f"rm -f {USB_EFI_MOUNT}/loader/random-seed", raise_on_nonzero_exit=False)
        self._runner.run_as_chroot(
            "systemctl mask systemd-boot-random-seed.service", raise_on_nonzero_exit=False
        )
        if not file_exists(self._runner, USB_BOOTLOADER_BINARIES[0]):
            self._copy_systemd_boot()

    def relocate_internal_efi(self) -> None:
        logger.info("Moving the EFI contents to the USB drive...")
        internal_efi = TARGET_EFI
        self._runner.run(f"rsync -a {internal_efi}/ {USB_EFI_MOUNT}/")
        self._runner.run(f"rm -rf {internal_efi}/EFI {internal_efi}/loader")

    def detach_luks_header(self) -> None:
        root_partition = self._config.storage.root_partition
        logger.info("Relocating the LUKS header of %s to the USB drive...", root_partition)
        self._runner.run(
            f"cryptsetup luksHeaderBackup {root_partition} "
            f"--header-backup-file {LUKS_HEADER_STAGING}"
        )
        self._runner.run(f"mkdir -p {USB_HEADER_MOUNT}")
        self._runner.run(f"mount {self.header_partition} {USB_HEADER_MOUNT}")
        self._runner.run(f"cp {LUKS_HEADER_STAGING} {USB_HEADER_MOUNT}/{LUKS_HEADER_IMAGE}")
        self._runner.run(f"chmod 600 {USB_HEADER_MOUNT}/{LUKS_HEADER_IMAGE}")
        # detach the on-disk header so the initrd reads it from the USB header partition
        self._runner.run(
            f"cryptsetup luksHeaderBackup {root_partition} --header-backup-file /dev/null",
            raise_on_nonzero_exit=False,
        )
        self._runner.run(
            f"cryptsetup erase --batch-mode {root_partition}", raise_on_nonzero_exit=False
        )
        self._runner.run(f"umount {USB_HEADER_MOUNT}", raise_on_nonzero_exit=False)
        self._runner.run(f"rm -f {LUKS_HEADER_STAGING}", raise_on_nonzero_exit=False)

    def install_recovery_iso(self) -> None:
        iso_path = self._resolve_iso_path()
        if not iso_path:
            logger.info("No Arch ISO found, skipping the recovery partition")
            return
        self._copy_iso(iso_path)
        self._install_recovery_entry(iso_path)

    def sign_binaries(self) -> None:
        if not file_exists(self._runner, f"{TARGET_ROOT}{SBCTL_DB_KEY}"):
            logger.info("No Secure Boot keys, skipping USB binary signing")
            return
        binaries = (
            *USB_BOOTLOADER_BINARIES,
            f"{USB_EFI_MOUNT}/EFI/recovery/archiso.efi",
        )
        for binary in binaries:
            if file_exists(self._runner, binary):
                self._runner.run_as_chroot(
                    f"sbctl sign -s {binary.replace(TARGET_ROOT, '')}",
                    raise_on_nonzero_exit=False,
                )

    def _validate_device(self, device: str) -> None:
        result = self._runner.run(f"lsblk -dno TYPE {device}", raise_on_nonzero_exit=False)
        if not result.success or "disk" not in result.stdout:
            raise RuntimeError(f"USB device {device} is not a disk")
        disk_name = device.rsplit("/", 1)[-1]
        removable = self._runner.run(
            f"cat /sys/block/{disk_name}/removable", raise_on_nonzero_exit=False
        )
        if removable.success and removable.stdout.strip() == "0":
            # virtio disks in a VM report non-removable, so this is a warning not a failure
            logger.warning("%s reports as non-removable, make sure it is the USB drive", device)

    def _partition(self, device: str) -> None:
        usb = self._config.usb_boot
        logger.info("Partitioning %s...", device)
        self._runner.run(f"wipefs -af {device}", raise_on_nonzero_exit=False)
        self._runner.run(f"sgdisk -Z {device}")
        self._runner.run(f"sgdisk -n1:0:+{usb.efi_size_mb}M -t1:ef00 {device}")
        self._runner.run(f"sgdisk -n2:0:+{usb.iso_partition_size_mb}M -t2:8300 {device}")
        self._runner.run(f"sgdisk -n3:0:+{LUKS_HEADER_PARTITION_SIZE_MB}M -t3:8300 {device}")
        if usb.backup_partition_size_mb > 0:
            self._runner.run(f"sgdisk -n4:0:+{usb.backup_partition_size_mb}M -t4:8300 {device}")
        else:
            self._runner.run(f"sgdisk -n4:0:0 -t4:8300 {device}")
        self._runner.run(f"partprobe {device}", raise_on_nonzero_exit=False)
        self._wait_for_partitions()

    def _wait_for_partitions(self) -> None:
        for _ in range(15):
            if path_exists(self._runner, self.backup_partition):
                return
            time.sleep(1)
            self._runner.run(f"partprobe {self.device}", raise_on_nonzero_exit=False)
        raise RuntimeError(f"USB partitions did not appear on {self.device}")

    def _format(self) -> None:
        logger.info("Formatting the USB partitions...")
        self._runner.run(f"mkfs.vfat -F32 -n USBBOOT {self.efi_partition}")
        self._runner.run(f"mkfs.ext4 -L ARCHISO -F {self.iso_partition}")
        self._runner.run(f"mkfs.ext4 -L LUKSHEADER -F {self.header_partition}")
        self._runner.run(f"mkfs.ext4 -L USBBACKUP -F {self.backup_partition}")

    def _copy_systemd_boot(self) -> None:
        for directory in ("EFI/BOOT", "EFI/systemd", "loader/entries"):
            self._runner.run(f"mkdir -p {USB_EFI_MOUNT}/{directory}")
        for source in SYSTEMD_BOOT_SOURCES:
            if file_exists(self._runner, source):
                self._runner.run(f"cp {source} {USB_EFI_MOUNT}/EFI/systemd/systemd-bootx64.efi")
                self._runner.run(f"cp {source} {USB_EFI_MOUNT}/EFI/BOOT/BOOTX64.EFI")
                return

    def _resolve_iso_path(self) -> str:
        candidates = (self._config.usb_boot.iso_path, *ISO_RECOVERY_SOURCES)
        for candidate in candidates:
            if candidate and file_exists(self._runner, candidate):
                return candidate
        return ""

    def _copy_iso(self, iso_path: str) -> None:
        logger.info("Copying the Arch ISO to the USB drive...")
        self._runner.run(f"mkdir -p {USB_ISO_MOUNT}")
        self._runner.run(f"mount {self.iso_partition} {USB_ISO_MOUNT}")
        self._runner.run(f"cp {iso_path} {USB_ISO_MOUNT}/archlinux.iso")
        self._runner.run(f"umount {USB_ISO_MOUNT}", raise_on_nonzero_exit=False)

    def _install_recovery_entry(self, iso_path: str) -> None:
        logger.info("Adding the recovery boot entry...")
        self._runner.run(f"mkdir -p {ISO_LOOP_MOUNT}")
        self._runner.run(f"mount -o loop {iso_path} {ISO_LOOP_MOUNT}", raise_on_nonzero_exit=False)
        if file_exists(self._runner, f"{ISO_LOOP_MOUNT}/EFI/BOOT/BOOTX64.EFI"):
            self._runner.run(f"mkdir -p {USB_EFI_MOUNT}/EFI/recovery")
            self._runner.run(
                f"cp {ISO_LOOP_MOUNT}/EFI/BOOT/BOOTX64.EFI {USB_EFI_MOUNT}/EFI/recovery/archiso.efi"
            )
        self._runner.run(f"umount {ISO_LOOP_MOUNT}", raise_on_nonzero_exit=False)
        self._runner.run(f"rmdir {ISO_LOOP_MOUNT}", raise_on_nonzero_exit=False)
        self._runner.run(f"mkdir -p {USB_EFI_MOUNT}/loader/entries")
        self._runner.run(
            f"cat > {USB_EFI_MOUNT}/loader/entries/archiso-recovery.conf",
            input_data=LIVE_ISO_BOOT_ENTRY,
        )


class UsbBootExecutor(Executor):
    def execute(self) -> None:
        drive = UsbBootDrive(self._config, self._runner)
        logger.info("Setting up the USB boot drive on %s...", drive.device)
        drive.provision()
        drive.install_bootloader()
        drive.relocate_internal_efi()
        if self._config.usb_boot.detached_luks_header:
            drive.detach_luks_header()
        drive.install_recovery_iso()
        drive.sign_binaries()
        self._runner.run(f"umount {USB_EFI_MOUNT}", raise_on_nonzero_exit=False)
        logger.info("USB boot drive ready")
