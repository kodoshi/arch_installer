import pytest

from arch_installer.config.loader import load_main_yaml_config
from arch_installer.config.models import DeclaredConfig
from arch_installer.errors import ConfigurationError


class TestConfigLoader:
    def test_should_load_config_when_valid_yaml_provided(self, test_config):
        assert isinstance(test_config, DeclaredConfig)
        assert test_config.system.hostname
        # target_disk can be empty (will be resolved at runtime from env var or prompt)
        assert len(test_config.packages.base) > 0

    def test_should_load_minimal_values_when_minimal_config_provided(self, minimal_config):
        assert minimal_config.system.hostname == "testhost"
        assert minimal_config.system.timezone == "UTC"
        assert minimal_config.storage.target_disk == "/dev/loop0"

    def test_should_raise_error_when_config_file_missing(self, tmp_path):
        with pytest.raises(ConfigurationError, match="Configuration file not found"):
            load_main_yaml_config(tmp_path / "nonexistent.yaml")

    def test_should_raise_error_when_yaml_is_invalid(self, tmp_path):
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: yaml: content: [")
        with pytest.raises(ConfigurationError, match="Failed to parse"):
            load_main_yaml_config(config_file)


class TestSystemConfig:
    def test_should_return_hostname_when_config_loaded(self, minimal_config):
        assert minimal_config.system.hostname == "testhost"

    def test_should_return_user_config_when_config_loaded(self, minimal_config):
        user = minimal_config.system.user
        assert user.name == "testuser"
        assert "wheel" in user.groups


class TestStorageConfig:
    def test_should_return_target_disk_when_config_loaded(self, minimal_config):
        assert minimal_config.storage.target_disk == "/dev/loop0"

    def test_should_return_luks_config_when_config_loaded(self, minimal_config):
        luks = minimal_config.storage.luks
        assert luks.type == "luks2"
        assert luks.pbkdf == "argon2id"
        assert luks.pbkdf_memory == 1048576

    def test_should_return_btrfs_subvolumes_when_config_loaded(self, minimal_config):
        subvolumes = minimal_config.storage.btrfs.subvolumes
        assert len(subvolumes) >= 2
        root_subvol = next(subvolume for subvolume in subvolumes if subvolume.mountpoint == "/")
        assert root_subvol.name == "@"

    def test_should_return_swap_config_when_config_loaded(self, minimal_config):
        swap = minimal_config.storage.swap
        assert swap.enabled is True
        assert swap.size_mb == 1024


class TestBootConfig:
    def test_should_return_kernel_config_when_config_loaded(self, minimal_config):
        kernels = minimal_config.boot.kernels
        assert len(kernels) >= 1
        assert kernels[0].name == "mainline"
        assert kernels[0].package == "linux"

    def test_should_return_cmdline_hardening_when_config_loaded(self, minimal_config):
        hardening = minimal_config.boot.cmdline.hardening
        assert hardening.lockdown == "integrity"
        assert hardening.pti == "on"

    def test_should_return_hooks_in_order_when_config_loaded(self, minimal_config):
        hooks = minimal_config.boot.hooks
        assert "systemd" in hooks
        assert "sd-encrypt" in hooks


class TestPackagesConfig:
    def test_should_return_profile_when_config_loaded(self, minimal_config):
        assert minimal_config.packages.profile == "base"

    def test_should_return_base_packages_when_config_loaded(self, minimal_config):
        base_pkgs = minimal_config.packages.base
        assert "base" in base_pkgs
        assert "linux" in base_pkgs


class TestGpuConfig:
    def test_should_have_gpu_disabled_when_minimal_config_used(self, minimal_config):
        assert minimal_config.gpu.enabled is False

    def test_should_return_gpu_vendor_when_config_loaded(self, minimal_config):
        assert minimal_config.gpu.vendor == "none"


class TestSnapperConfig:
    def test_should_have_snapper_enabled_when_config_loaded(self, minimal_config):
        assert minimal_config.snapper.enabled is True

    def test_should_return_retention_policy_when_config_loaded(self, minimal_config):
        root_retention = minimal_config.snapper.root.retention
        assert root_retention.hourly == 5
        assert root_retention.daily == 7


class TestDockerConfig:
    def test_should_have_docker_disabled_when_minimal_config_used(self, minimal_config):
        assert minimal_config.docker.enabled is False

    def test_should_return_docker_defaults_when_config_loaded(self, minimal_config):
        docker = minimal_config.docker
        assert docker.storage_driver == "overlay2"
        assert docker.data_root == "/var/lib/docker"


class TestConfigImmutability:
    def test_should_raise_error_when_mutating_frozen_config(self, minimal_config):
        with pytest.raises(Exception):
            minimal_config.system.hostname = "changed"

    def test_should_raise_error_when_mutating_nested_frozen_config(self, minimal_config):
        with pytest.raises(Exception):
            minimal_config.storage.luks.type = "luks1"
