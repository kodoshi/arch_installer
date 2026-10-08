from dataclasses import replace

from arch_installer.config.models import WipeMethod
from arch_installer.executors.storage import StorageStepExecutor
from tests.unit.conftest import build_config


def storage_runner(fake_runner):
    # a fresh disk: nothing mounted, no partitions, no existing LUKS
    fake_runner.set_default_response(exit_code=0)
    fake_runner.set_response("mountpoint", exit_code=1)
    fake_runner.set_response("lsblk", exit_code=1)
    fake_runner.set_response("cryptsetup status", exit_code=4)
    fake_runner.set_response("cryptsetup isLuks", exit_code=1)
    fake_runner.set_response("test -e", exit_code=0)
    return fake_runner


class TestStorageExecutor:
    def test_wipes_and_partitions_a_fresh_disk(self, fake_runner):
        config = build_config(storage=replace(build_config().storage, target_disk="/dev/sda"))
        StorageStepExecutor(config, storage_runner(fake_runner)).execute()

        fake_runner.assert_command_called("wipefs -af /dev/sda")
        fake_runner.assert_command_called("sgdisk -n1:0:+512M -t1:ef00 /dev/sda")
        fake_runner.assert_command_called("sgdisk -n2:0:0 -t2:8304 /dev/sda")

    def test_luks_format_uses_the_configured_cipher_and_hash(self, fake_runner):
        config = build_config(storage=replace(build_config().storage, target_disk="/dev/sda"))
        StorageStepExecutor(config, storage_runner(fake_runner)).execute()

        luks_format = fake_runner.get_commands("luksFormat")
        assert luks_format
        assert "--cipher aes-xts-plain64" in luks_format[0]
        assert "--hash sha512" in luks_format[0]
        assert "--key-size 512" in luks_format[0]

    def test_creates_the_configured_btrfs_subvolumes(self, fake_runner):
        config = build_config(storage=replace(build_config().storage, target_disk="/dev/sda"))
        StorageStepExecutor(config, storage_runner(fake_runner)).execute()

        subvolume_commands = fake_runner.get_commands("subvolume create")
        created = " ".join(subvolume_commands)
        assert "@" in created and "@home" in created

    def test_skips_when_target_is_already_mounted(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("mountpoint", exit_code=0)
        config = build_config(storage=replace(build_config().storage, target_disk="/dev/sda"))

        StorageStepExecutor(config, fake_runner).execute()

        fake_runner.assert_command_not_called("sgdisk")

    def test_migration_forces_a_quick_wipe_over_the_configured_method(self, fake_runner):
        base = build_config()
        config = build_config(
            storage=replace(
                build_config().storage, target_disk="/dev/sda", wipe_method=WipeMethod.SECURE
            ),
            migration=replace(base.migration, enabled=True),
        )
        StorageStepExecutor(config, storage_runner(fake_runner)).execute()

        # secure wipe would call shred; a quick wipe never does
        fake_runner.assert_command_not_called("shred")
        fake_runner.assert_command_called("sgdisk -Z /dev/sda")


def usb_boot_storage(**storage_overrides):
    base = build_config()
    return build_config(
        storage=replace(
            base.storage,
            target_disk="/dev/vda",
            wipe_method=storage_overrides.get("wipe_method", WipeMethod.SECURE),
        ),
        usb_boot=replace(base.usb_boot, enabled=True, device="/dev/sdb"),
        migration=replace(base.migration, enabled=storage_overrides.get("migration", False)),
    )


class TestStorageWithUsbBootDrive:
    def test_internal_disk_gets_no_partition_table(self, fake_runner):
        StorageStepExecutor(usb_boot_storage(), storage_runner(fake_runner)).execute()

        fake_runner.assert_command_not_called("sgdisk -n")
        fake_runner.assert_command_not_called("mkfs.vfat -F32 -n EFI /dev/vda")

    def test_random_fill_covers_the_erased_partition_table(self, fake_runner):
        StorageStepExecutor(usb_boot_storage(), storage_runner(fake_runner)).execute()

        commands = fake_runner.get_commands()
        zap = commands.index("sgdisk -Z /dev/vda")
        fill = commands.index("shred -v -n 1 /dev/vda")
        assert zap < fill

    def test_luks_header_is_formatted_onto_the_drive(self, fake_runner):
        StorageStepExecutor(usb_boot_storage(), storage_runner(fake_runner)).execute()

        luks_format = fake_runner.get_commands("luksFormat")
        assert len(luks_format) == 1
        assert luks_format[0].endswith("--header /dev/sdb2 --offset 0 --key-file - /dev/vda")
        fake_runner.assert_command_called("cryptsetup isLuks /dev/sdb2")
        fake_runner.assert_command_called(
            "cryptsetup open --header /dev/sdb2 --key-file - /dev/vda cryptroot"
        )

    def test_an_open_volume_on_the_whole_disk_is_kept(self, fake_runner):
        storage_runner(fake_runner)
        fake_runner.set_response(
            "cryptsetup status", stdout="/dev/mapper/cryptroot is active.\n  device:  /dev/vda\n"
        )
        config = usb_boot_storage(wipe_method=WipeMethod.SKIP)

        StorageStepExecutor(config, fake_runner).execute()

        fake_runner.assert_command_not_called("luksFormat")
        fake_runner.assert_command_not_called("cryptsetup open")

    def test_drive_efi_partition_becomes_the_system_esp(self, fake_runner):
        StorageStepExecutor(usb_boot_storage(), storage_runner(fake_runner)).execute()

        fake_runner.assert_command_called("mount -o umask=0077 /dev/sdb1 /mnt/efi")

    def test_migration_fills_the_disk_with_random_data(self, fake_runner):
        config = usb_boot_storage(wipe_method=WipeMethod.SKIP, migration=True)

        StorageStepExecutor(config, storage_runner(fake_runner)).execute()

        fake_runner.assert_command_called("shred -v -n 1 /dev/vda")
