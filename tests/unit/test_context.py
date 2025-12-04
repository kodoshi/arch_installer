from arch_installer.core.runtime_state import create_default_runtime_config


class TestRuntimeConfig:
    def test_should_create_state_when_using_factory(self):
        state = create_default_runtime_config()
        assert state is not None
        assert state.target_disk == ""
        assert state.luks_password == ""
        assert state.source_luks_password == ""

    def test_should_return_default_values_when_using_runtime_state_fixture(self, runtime_state):
        assert runtime_state.target_disk is not None
        assert runtime_state.gpu_vendor == ""
        assert runtime_state.selected_kernels == ["linux"]

    def test_should_track_partitions_when_partition_values_set(self, runtime_state):
        runtime_state.efi_partition = "/dev/loop0p1"
        runtime_state.root_partition = "/dev/loop0p2"

        assert runtime_state.efi_partition == "/dev/loop0p1"
        assert runtime_state.root_partition == "/dev/loop0p2"

    def test_should_track_feature_flags_when_flags_set(self, runtime_state):
        runtime_state.non_interactive = True
        runtime_state.enable_firewall = True

        assert runtime_state.non_interactive is True
        assert runtime_state.enable_firewall is True

    def test_should_track_selected_components_when_components_set(self, runtime_state):
        runtime_state.selected_kernels = ["linux", "linux-lts"]
        runtime_state.selected_desktops = ["kde"]

        assert "linux" in runtime_state.selected_kernels
        assert "linux-lts" in runtime_state.selected_kernels
        assert "kde" in runtime_state.selected_desktops

    def test_should_store_passwords_when_passwords_set(self, runtime_state):
        assert runtime_state.luks_password == "testpassword"
        assert runtime_state.user_password == "userpassword"

    def test_should_generate_partition_paths_when_nvme_device_used(self):
        state = create_default_runtime_config()
        state.target_disk = "/dev/nvme0n1"

        assert state.efi_partition == "/dev/nvme0n1p1"
        assert state.root_partition == "/dev/nvme0n1p2"

    def test_should_generate_partition_paths_when_sata_device_used(self):
        state = create_default_runtime_config()
        state.target_disk = "/dev/sda"

        assert state.efi_partition == "/dev/sda1"
        assert state.root_partition == "/dev/sda2"

    def test_should_return_cryptroot_device_when_accessing_property(self, runtime_state):
        assert runtime_state.cryptroot_device == "/dev/mapper/cryptroot"
