from dataclasses import replace

from arch_installer.config.models import StorageConfig, WipeMethod
from arch_installer.executors.storage import StorageExecutor
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
        StorageExecutor(config, storage_runner(fake_runner)).execute()

        fake_runner.assert_command_called("wipefs -af /dev/sda")
        fake_runner.assert_command_called("sgdisk -n1:0:+512M -t1:ef00 /dev/sda")
        fake_runner.assert_command_called("sgdisk -n2:0:0 -t2:8304 /dev/sda")

    def test_luks_format_uses_the_configured_cipher_and_hash(self, fake_runner):
        config = build_config(storage=replace(build_config().storage, target_disk="/dev/sda"))
        StorageExecutor(config, storage_runner(fake_runner)).execute()

        luks_format = fake_runner.get_commands("luksFormat")
        assert luks_format
        assert "--cipher aes-xts-plain64" in luks_format[0]
        assert "--hash sha512" in luks_format[0]
        assert "--key-size 512" in luks_format[0]

    def test_creates_the_configured_btrfs_subvolumes(self, fake_runner):
        config = build_config(storage=replace(build_config().storage, target_disk="/dev/sda"))
        StorageExecutor(config, storage_runner(fake_runner)).execute()

        subvolume_commands = fake_runner.get_commands("subvolume create")
        created = " ".join(subvolume_commands)
        assert "@" in created and "@home" in created

    def test_skips_when_target_is_already_mounted(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("mountpoint", exit_code=0)
        config = build_config(storage=replace(build_config().storage, target_disk="/dev/sda"))

        StorageExecutor(config, fake_runner).execute()

        fake_runner.assert_command_not_called("sgdisk")

    def test_migration_forces_a_quick_wipe_over_the_configured_method(self, fake_runner):
        base = build_config()
        config = build_config(
            storage=StorageConfig(target_disk="/dev/sda", wipe_method=WipeMethod.SECURE),
            migration=replace(base.migration, enabled=True),
        )
        StorageExecutor(config, storage_runner(fake_runner)).execute()

        # secure wipe would call shred; a quick wipe never does
        fake_runner.assert_command_not_called("shred")
        fake_runner.assert_command_called("sgdisk -Z /dev/sda")
