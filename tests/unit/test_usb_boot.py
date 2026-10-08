from dataclasses import replace
from pathlib import Path

import pytest

from arch_installer.config.models import InstallerConfig, WipeMethod
from arch_installer.errors import UsbBootDriveError
from arch_installer.executors.base import stable_disk_path
from arch_installer.executors.recovery import (
    CERTIFICATE_INITRAMFS,
    RECOVERY_BUILD_DIRECTORY,
    RECOVERY_ENTRY,
    RECOVERY_SIGNING_DIRECTORY,
    RECOVERY_UKI,
    SIGNING_CERTIFICATE_DAYS,
    RecoverySystemStepExecutor,
    recovery_cmdline,
)
from arch_installer.executors.usb_boot import (
    RECOVERY_MOUNT,
    SNAPSHOT_REFRESH_PENDING_MARKER,
    USB_BOOT_GUARD_HOOK,
    USB_BOOT_SETTINGS_FILE,
    UsbBootDriveCloner,
    UsbBootDriveStepExecutor,
    UsbBootSafeguardsStepExecutor,
    recovery_partition_size_mb,
    removable_efi_fstab,
)
from tests.unit import FakeCommandRunner
from tests.unit.conftest import build_config

SNAPSHOT_MANAGER_SCRIPT = Path(__file__).parents[2] / "scripts" / "manage_snapshot_entries.sh"
# the 2026.01 Arch ISO: 751304 blocks of 2048 bytes
ISO_BLOCK_COUNT = 751304
ISO_SIZE_BYTES = ISO_BLOCK_COUNT * 2048
EIGHT_GIBIBYTES = 8 * 1024**3


def usb_boot_install(
    recovery_system: bool = True, wipe_method: WipeMethod = WipeMethod.SECURE
) -> InstallerConfig:
    base = build_config()
    return build_config(
        storage=replace(base.storage, target_disk="/dev/vda", wipe_method=wipe_method),
        boot=replace(base.boot, enable_snapshot_boot=True),
        usb_boot=replace(
            base.usb_boot,
            enabled=True,
            device="/dev/sdb",
            recovery_system=recovery_system,
            iso_path="/dev/sr0",
        ),
    )


def fresh_usb_drive(runner: FakeCommandRunner, size_bytes: int = EIGHT_GIBIBYTES) -> None:
    runner.set_default_response(exit_code=0)
    runner.set_response("lsblk -dno TYPE", stdout="disk\n")
    runner.set_response("cat /sys/block/sdb/removable", stdout="1\n")
    runner.set_response("mountpoint", exit_code=1)
    runner.set_response("blockdev --getsize64 /dev/sdb", stdout=f"{size_bytes}\n")
    runner.set_response("skip=32769", stdout="CD001")
    runner.set_response("skip=32848", stdout=f"  {ISO_BLOCK_COUNT}\n")
    runner.set_response("skip=32896", stdout="  2048\n")
    runner.set_response("/dev/disk/by-id/*", stdout="/dev/disk/by-id/nvme-Disk_S123\n")


class TestUsbBootDriveLayout:
    def test_holds_the_efi_header_and_recovery_partitions(self, fake_runner):
        fresh_usb_drive(fake_runner)

        UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()

        fake_runner.assert_command_called("sgdisk -n1:0:+512M -t1:ef00 /dev/sdb")
        fake_runner.assert_command_called("sgdisk -n2:0:+32M -t2:8300 /dev/sdb")
        fake_runner.assert_command_called(
            f"sgdisk -n3:0:+{recovery_partition_size_mb(ISO_SIZE_BYTES)}M -t3:8300 /dev/sdb"
        )
        fake_runner.assert_command_called("mkfs.vfat -F32 -n EFI /dev/sdb1")

    def test_copies_the_live_system_of_the_live_medium(self, fake_runner):
        fresh_usb_drive(fake_runner)

        UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()

        fake_runner.assert_command_called("mkfs.ext4 -F -m 0 -L RECOVERY /dev/sdb3")
        fake_runner.assert_command_called(f"bsdtar -xf /dev/sr0 -C {RECOVERY_MOUNT} arch")

    def test_without_the_recovery_system_the_iso_is_left_alone(self, fake_runner):
        fresh_usb_drive(fake_runner)

        UsbBootDriveStepExecutor(usb_boot_install(recovery_system=False), fake_runner).execute()

        fake_runner.assert_command_not_called("sgdisk -n3")
        fake_runner.assert_command_not_called("/dev/sr0")

    def test_recovery_partition_leaves_room_for_the_filesystem(self):
        size_mb = recovery_partition_size_mb(ISO_SIZE_BYTES)
        assert size_mb * 1024 * 1024 > ISO_SIZE_BYTES * 1.1


class TestInternalDiskName:
    def test_prefers_the_worldwide_unique_identifier(self, fake_runner):
        fake_runner.set_response(
            "/dev/disk/by-id/*",
            stdout="/dev/disk/by-id/nvme-Samsung_990_S6Z1\n/dev/disk/by-id/nvme-eui.0025385\n",
        )

        assert stable_disk_path(fake_runner, "/dev/nvme0n1") == "/dev/disk/by-id/nvme-eui.0025385"

    def test_falls_back_to_the_model_and_serial_name(self, fake_runner):
        fake_runner.set_response(
            "/dev/disk/by-id/*",
            stdout="/dev/disk/by-id/virtio-dali-disk-1\n/dev/disk/by-id/virtio-dali-disk-0\n",
        )

        assert stable_disk_path(fake_runner, "/dev/vda") == "/dev/disk/by-id/virtio-dali-disk-0"

    def test_is_empty_for_a_disk_udev_cannot_name(self, fake_runner):
        fake_runner.set_response("/dev/disk/by-id/*", stdout="")

        assert stable_disk_path(fake_runner, "/dev/vda") == ""


class TestUsbBootDriveRefusals:
    def test_refuses_the_target_disk(self, fake_runner):
        fresh_usb_drive(fake_runner)
        config = usb_boot_install()
        config = replace(config, usb_boot=replace(config.usb_boot, device="/dev/vda"))

        with pytest.raises(UsbBootDriveError, match="target disk"):
            UsbBootDriveStepExecutor(config, fake_runner).execute()

    def test_refuses_a_target_disk_the_boot_could_not_find(self, fake_runner):
        fresh_usb_drive(fake_runner)
        fake_runner.set_response("/dev/disk/by-id/*", stdout="")

        with pytest.raises(UsbBootDriveError, match="no /dev/disk/by-id name"):
            UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()
        fake_runner.assert_command_not_called("wipefs")

    def test_refuses_a_partition(self, fake_runner):
        fresh_usb_drive(fake_runner)
        fake_runner.set_response("lsblk -dno TYPE", stdout="part\n")

        with pytest.raises(UsbBootDriveError, match="not a disk"):
            UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()

    def test_refuses_an_image_that_is_no_iso_before_touching_the_drive(self, fake_runner):
        fresh_usb_drive(fake_runner)
        fake_runner.set_response("skip=32769", stdout="\x00\x00\x00\x00\x00")

        with pytest.raises(UsbBootDriveError, match="not an ISO 9660 image"):
            UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()
        fake_runner.assert_command_not_called("sgdisk")

    def test_refuses_an_iso_without_the_arch_live_system(self, fake_runner):
        fresh_usb_drive(fake_runner)
        fake_runner.set_response("bsdtar -tf", exit_code=1)

        with pytest.raises(UsbBootDriveError, match="not an Arch Linux ISO"):
            UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()
        fake_runner.assert_command_not_called("wipefs")

    def test_refuses_a_drive_too_small_before_wiping_it(self, fake_runner):
        fresh_usb_drive(fake_runner, size_bytes=1024**3)

        with pytest.raises(UsbBootDriveError, match="needs"):
            UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()
        fake_runner.assert_command_not_called("wipefs")


class TestResumedInstallation:
    def test_keeps_the_drive_whose_header_unlocks_the_mounted_target(self, fake_runner):
        fresh_usb_drive(fake_runner)
        fake_runner.set_response("mountpoint", exit_code=0)

        UsbBootDriveStepExecutor(usb_boot_install(), fake_runner).execute()

        fake_runner.assert_command_not_called("sgdisk")

    def test_keeps_the_drive_when_the_disk_is_not_wiped(self, fake_runner):
        fresh_usb_drive(fake_runner)

        UsbBootDriveStepExecutor(
            usb_boot_install(wipe_method=WipeMethod.SKIP), fake_runner
        ).execute()

        fake_runner.assert_command_not_called("sgdisk")

    def test_prepares_the_drive_again_when_it_holds_no_header(self, fake_runner):
        fresh_usb_drive(fake_runner)
        fake_runner.set_response("cryptsetup isLuks /dev/sdb2", exit_code=1)

        UsbBootDriveStepExecutor(
            usb_boot_install(wipe_method=WipeMethod.SKIP), fake_runner
        ).execute()

        fake_runner.assert_command_called("sgdisk -Z /dev/sdb")


def installed_usb_boot_system(runner: FakeCommandRunner, fstab: str) -> None:
    runner.set_default_response(exit_code=0)
    runner.set_response("blkid -s PARTUUID -o value /dev/sdb1", stdout="1111-efi\n")
    runner.set_response("cat /mnt/etc/fstab", stdout=fstab)


GENERATED_FSTAB = """# /dev/mapper/cryptroot
UUID=aaaa\t/\tbtrfs\trw,subvol=/@\t0 0
# /dev/sdb1
UUID=ABCD-1234\t/efi\tvfat\trw,fmask=0077,dmask=0077\t0 2
"""


class TestUsbBootSafeguards:
    def test_efi_is_mounted_on_demand_by_partition_uuid(self):
        fstab = removable_efi_fstab(GENERATED_FSTAB, "1111-efi")

        efi_lines = [line for line in fstab.splitlines() if "\t/efi\t" in line]
        assert efi_lines == [
            "PARTUUID=1111-efi\t/efi\tvfat\tumask=0077,noauto,nofail,x-systemd.automount,"
            "x-systemd.idle-timeout=60s,x-systemd.device-timeout=5s\t0 2"
        ]
        assert "UUID=aaaa\t/\tbtrfs" in fstab

    def test_pacman_refuses_boot_file_updates_without_the_drive(self, fake_runner):
        installed_usb_boot_system(fake_runner, GENERATED_FSTAB)

        UsbBootSafeguardsStepExecutor(usb_boot_install(), fake_runner).execute()

        hook = fake_runner.written_content(f"/mnt{USB_BOOT_GUARD_HOOK}")
        assert "When = PreTransaction" in hook
        assert "AbortOnFail" in hook
        assert "Target = usr/lib/modules/*/vmlinuz" in hook
        assert "Target = boot/*" in hook
        settings = fake_runner.written_content(f"/mnt{USB_BOOT_SETTINGS_FILE}")
        assert "EFI_PARTITION_UUID=1111-efi" in settings

    def test_plugging_the_drive_in_builds_the_skipped_snapshot_ukis(self, fake_runner):
        installed_usb_boot_system(fake_runner, GENERATED_FSTAB)

        UsbBootSafeguardsStepExecutor(usb_boot_install(), fake_runner).execute()

        rule = fake_runner.written_content("/mnt/etc/udev/rules.d/90-usb-boot-drive.rules")
        assert 'ENV{ID_PART_ENTRY_UUID}=="1111-efi"' in rule
        assert "snapshot-ukis-catch-up.service" in rule
        service = fake_runner.written_content(
            "/mnt/etc/systemd/system/snapshot-ukis-catch-up.service"
        )
        assert f"ConditionPathExists={SNAPSHOT_REFRESH_PENDING_MARKER}" in service

    def test_no_snapshot_catch_up_without_bootable_snapshots(self, fake_runner):
        installed_usb_boot_system(fake_runner, GENERATED_FSTAB)
        config = usb_boot_install()
        config = replace(config, boot=replace(config.boot, enable_snapshot_boot=False))

        UsbBootSafeguardsStepExecutor(config, fake_runner).execute()

        fake_runner.assert_command_not_called("90-usb-boot-drive.rules")

    def test_snapshot_manager_leaves_the_marker_the_catch_up_waits_for(self):
        script = SNAPSHOT_MANAGER_SCRIPT.read_text()
        assert f'REFRESH_PENDING_MARKER="{SNAPSHOT_REFRESH_PENDING_MARKER}"' in script


def two_usb_drives(runner: FakeCommandRunner) -> None:
    runner.set_default_response(exit_code=0)
    runner.set_response("lsblk -dno TYPE", stdout="disk\n")
    runner.set_response("lsblk -nro MOUNTPOINT", stdout="\n\n")


class TestSpareUsbBootDrive:
    def test_copies_the_partition_table_and_every_partition(self, fake_runner):
        two_usb_drives(fake_runner)

        UsbBootDriveCloner(fake_runner).clone("/dev/sdb", "/dev/sdc")

        fake_runner.assert_command_called("sgdisk --replicate=/dev/sdc /dev/sdb")
        for number in (1, 2, 3):
            fake_runner.assert_command_called(f"dd if=/dev/sdb{number} of=/dev/sdc{number}")
        fake_runner.assert_command_called("cmp /dev/sdb2 /dev/sdc2")

    def test_refuses_one_drive_as_both(self, fake_runner):
        two_usb_drives(fake_runner)

        with pytest.raises(UsbBootDriveError, match="another drive"):
            UsbBootDriveCloner(fake_runner).clone("/dev/sdb", "/dev/sdb")

    def test_refuses_a_source_without_a_luks_header(self, fake_runner):
        two_usb_drives(fake_runner)
        fake_runner.set_response("cryptsetup isLuks", exit_code=1)

        with pytest.raises(UsbBootDriveError, match="not a USB boot drive"):
            UsbBootDriveCloner(fake_runner).clone("/dev/sdb", "/dev/sdc")
        fake_runner.assert_command_not_called("sgdisk")

    def test_refuses_a_spare_in_use(self, fake_runner):
        two_usb_drives(fake_runner)
        fake_runner.set_response("lsblk -nro MOUNTPOINT", stdout="\n/media/stick\n")

        with pytest.raises(UsbBootDriveError, match="mounted"):
            UsbBootDriveCloner(fake_runner).clone("/dev/sdb", "/dev/sdc")


class TestRecoverySystem:
    def test_boots_the_live_system_and_verifies_its_root_image(self):
        cmdline = recovery_cmdline("2222-recovery")
        assert "archisodevice=UUID=2222-recovery" in cmdline
        assert "archisobasedir=arch" in cmdline
        assert "cms_verify=y" in cmdline

    def test_signs_the_root_image_with_a_key_it_then_discards(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        RecoverySystemStepExecutor(usb_boot_install(), fake_runner).execute()

        commands = fake_runner.get_commands()
        signing = [
            index for index, command in enumerate(commands) if "openssl cms -sign" in command
        ]
        key_removal = [
            index
            for index, command in enumerate(commands)
            if command == f"rm -f {RECOVERY_SIGNING_DIRECTORY}/key.pem"
        ]
        assert len(signing) == 1 and key_removal and key_removal[0] > signing[0]
        assert f"-out {RECOVERY_MOUNT}/arch/x86_64/airootfs.sfs.cms.sig" in commands[signing[0]]
        assert f"-days {SIGNING_CERTIFICATE_DAYS}" in fake_runner.get_commands("openssl req")[0]

    def test_uki_carries_the_certificate_after_the_iso_initramfs(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("blkid -s UUID -o value /dev/sdb3", stdout="2222-recovery\n")

        RecoverySystemStepExecutor(usb_boot_install(), fake_runner).execute()

        ukify = fake_runner.get_commands("ukify build")
        assert len(ukify) == 1
        initramfs_images = [part for part in ukify[0].split() if part.startswith("--initrd=")]
        assert initramfs_images == [
            f"--initrd={RECOVERY_BUILD_DIRECTORY}/initramfs-linux.img",
            f"--initrd={RECOVERY_BUILD_DIRECTORY}/{CERTIFICATE_INITRAMFS}",
        ]
        assert recovery_cmdline("2222-recovery") in ukify[0]
        assert f"--output={RECOVERY_UKI}" in ukify[0]

    def test_lists_and_signs_the_recovery_uki(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("cat /run/dali/usb-recovery/arch/version", stdout="2026.01.01\n")

        RecoverySystemStepExecutor(usb_boot_install(), fake_runner).execute()

        entry = fake_runner.written_content(f"/mnt{RECOVERY_ENTRY}")
        assert "title    Arch Linux recovery (2026.01.01)" in entry
        assert "efi      /EFI/recovery/arch-recovery.efi" in entry
        fake_runner.assert_command_called(f"sbctl sign -s {RECOVERY_UKI}")

    def test_without_secure_boot_keys_the_uki_stays_unsigned(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("test -f", exit_code=1)

        RecoverySystemStepExecutor(usb_boot_install(), fake_runner).execute()

        fake_runner.assert_command_not_called("sbctl sign")
