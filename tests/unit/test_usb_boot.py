from dataclasses import replace

from arch_installer.config.models import UsbBootConfig
from arch_installer.executors.usb_boot import USB_EFI_MOUNT, UsbBootDrive, UsbBootExecutor
from tests.unit.conftest import build_config


def usb_config(**overrides) -> object:
    return build_config(usb_boot=UsbBootConfig(enabled=True, device="/dev/sdb", **overrides))


def provisioned_runner(fake_runner):
    fake_runner.set_default_response(exit_code=0)
    fake_runner.set_response("lsblk -dno TYPE /dev/sdb", stdout="disk")
    fake_runner.set_response("cat /sys/block", stdout="1")
    fake_runner.set_response("test -e /dev/sdb4", exit_code=0)
    fake_runner.set_response("test -f", exit_code=1)
    return fake_runner


class TestUsbPartitionPaths:
    def test_derives_the_four_partitions(self):
        drive = UsbBootDrive(usb_config(), None)
        assert drive.efi_partition == "/dev/sdb1"
        assert drive.iso_partition == "/dev/sdb2"
        assert drive.header_partition == "/dev/sdb3"
        assert drive.backup_partition == "/dev/sdb4"

    def test_nvme_uses_the_p_separator(self):
        drive = UsbBootDrive(
            build_config(usb_boot=UsbBootConfig(enabled=True, device="/dev/nvme0n1")), None
        )
        assert drive.efi_partition == "/dev/nvme0n1p1"


class TestUsbBootDriveProvisioning:
    def test_partitions_and_formats_the_four_partitions(self, fake_runner):
        UsbBootDrive(usb_config(), provisioned_runner(fake_runner)).provision()
        fake_runner.assert_command_called("sgdisk -n1:0:+512M -t1:ef00 /dev/sdb")
        fake_runner.assert_command_called("mkfs.vfat -F32 -n USBBOOT /dev/sdb1")
        fake_runner.assert_command_called("mkfs.ext4 -L LUKSHEADER -F /dev/sdb3")
        fake_runner.assert_command_called("mkfs.ext4 -L USBBACKUP -F /dev/sdb4")

    def test_rejects_a_non_disk_device(self, fake_runner):
        fake_runner.set_response("lsblk -dno TYPE /dev/sdb", stdout="part")
        try:
            UsbBootDrive(usb_config(), fake_runner).provision()
            raise AssertionError("expected a RuntimeError for a non-disk device")
        except RuntimeError as error:
            assert "not a disk" in str(error)

    def test_relocates_the_internal_efi(self, fake_runner):
        provisioned_runner(fake_runner)
        UsbBootDrive(usb_config(), fake_runner).relocate_internal_efi()
        assert any(
            "rsync" in command and USB_EFI_MOUNT in command
            for command in fake_runner.get_commands()
        )

    def test_detaches_the_luks_header_to_the_header_partition(self, fake_runner):
        provisioned_runner(fake_runner)
        config = build_config(
            storage=replace(build_config().storage, target_disk="/dev/vda"),
            usb_boot=UsbBootConfig(enabled=True, device="/dev/sdb", detached_luks_header=True),
        )
        UsbBootDrive(config, fake_runner).detach_luks_header()
        assert any(
            "luksHeaderBackup /dev/vda2" in command for command in fake_runner.get_commands()
        )
        fake_runner.assert_command_called("mount /dev/sdb3 /mnt/usb-header")


class TestUsbBootExecutor:
    def test_runs_the_full_provision_sequence(self, fake_runner):
        config = build_config(
            storage=replace(build_config().storage, target_disk="/dev/vda"),
            usb_boot=UsbBootConfig(enabled=True, device="/dev/sdb", detached_luks_header=False),
        )
        UsbBootExecutor(config, provisioned_runner(fake_runner)).execute()
        fake_runner.assert_command_called("sgdisk -n1:0:+512M -t1:ef00 /dev/sdb")
        fake_runner.assert_command_called("bootctl install --esp-path=/mnt/usb-efi --no-variables")
