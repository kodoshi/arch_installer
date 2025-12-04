import pytest

from arch_installer.steps.storage import StorageProvisioner, WipeMethod


class TestStorageProvisioner:
    @pytest.fixture
    def provisioner(self, minimal_config, runtime_state, fake_runner):
        return StorageProvisioner(minimal_config, runtime_state, fake_runner)

    def test_should_create_partitions_when_provisioning_non_loop_device(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.target_disk = "/dev/sda"
        provisioner = StorageProvisioner(minimal_config, runtime_state, fake_runner)

        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("mountpoint", exit_code=1)
        fake_runner.set_response("lsblk /dev/sda", exit_code=1)
        fake_runner.set_response("cryptsetup status", exit_code=4)

        provisioner.provision_storage()

        fake_runner.assert_command_called("sgdisk")

    def test_should_wipe_disk_when_quick_wipe_method_used(self, provisioner, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("mountpoint", exit_code=1)

        provisioner._wipe_disk(WipeMethod.QUICK)

        fake_runner.assert_command_called("wipefs")
        fake_runner.assert_command_called("sgdisk")

    def test_should_create_btrfs_subvolumes_when_configuring_storage(
        self, provisioner, fake_runner
    ):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("test -d", exit_code=1)

        provisioner._create_subvolumes()

        btrfs_commands = fake_runner.get_commands("btrfs")
        assert any("subvolume create" in command for command in btrfs_commands)
