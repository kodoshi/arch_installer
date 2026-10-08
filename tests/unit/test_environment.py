import pytest

from arch_installer.config.environment import Environment, EnvVar, unlock_secrets
from arch_installer.config.models import (
    CpuVendor,
    Credentials,
    Desktop,
    EncryptedSecretsConfig,
    FirewallConfig,
    GpuVendor,
    WipeMethod,
)
from arch_installer.core.secrets import encrypt_secret
from arch_installer.errors import ConfigurationError
from tests.unit.conftest import build_config


def env(**variables: str) -> Environment:
    return Environment(variables)


class TestEnvironmentOverride:
    def test_environment_overrides_config_values(self, test_config):
        result = env(TARGET_DISK="/dev/nvme0n1", WIPE_METHOD="secure").override(test_config)
        assert result.storage.target_disk == "/dev/nvme0n1"
        assert result.storage.wipe_method == WipeMethod.SECURE

    def test_unset_variable_keeps_the_config_value(self, test_config):
        result = env().override(test_config)
        assert result.storage.target_disk == test_config.storage.target_disk
        assert result.firewall.enabled == test_config.firewall.enabled

    def test_skip_swap_flag_disables_swap(self, test_config):
        assert test_config.storage.swap.enabled
        assert not env(SKIP_SWAP="true").override(test_config).storage.swap.enabled

    def test_enable_flag_turns_a_feature_on(self):
        config = build_config(firewall=FirewallConfig(enabled=False))
        assert env(ENABLE_FIREWALL="true").override(config).firewall.enabled

    def test_list_variables_parse_to_tuples(self, test_config):
        result = env(SELECTED_DESKTOPS="gnome,kde").override(test_config)
        assert result.packages.selected_desktops == (Desktop.GNOME, Desktop.KDE)

    def test_choice_variables_parse_to_enums(self, test_config):
        result = env(CPU_VENDOR="intel", GPU_VENDOR="nvidia").override(test_config)
        assert result.system.cpu_vendor == CpuVendor.INTEL
        assert result.gpu.vendor == GpuVendor.NVIDIA

    def test_invalid_choice_is_rejected_with_the_variable_name(self, test_config):
        with pytest.raises(ConfigurationError, match="GPU_VENDOR"):
            env(GPU_VENDOR="banana").override(test_config)

    def test_invalid_flag_is_rejected(self, test_config):
        with pytest.raises(ConfigurationError, match="ENABLE_DOCKER"):
            env(ENABLE_DOCKER="maybe").override(test_config)

    def test_passwords_come_from_the_environment(self, test_config):
        result = env(LUKS_PASSWORD="disk", USER_PASSWORD="user").override(test_config)
        assert result.credentials.luks_password == "disk"
        assert result.credentials.user_password == "user"


class TestEnvVarNames:
    def test_every_binding_variable_is_an_envvar_member(self):
        # the single source of truth for names is the enum
        assert EnvVar.TARGET_DISK == "TARGET_DISK"
        assert EnvVar.SECRETS_KEY == "ARCH_INSTALLER_SECRETS_KEY"


class TestUnlockSecrets:
    def test_decrypts_both_passwords_into_credentials(self):
        key = "k"
        config = build_config(
            secrets=EncryptedSecretsConfig(
                luks_password_encrypted=encrypt_secret("disk-pw", key),
                user_password_encrypted=encrypt_secret("user-pw", key),
            ),
            credentials=Credentials(),
        )
        unlocked = unlock_secrets(config, key)
        assert unlocked.credentials.luks_password == "disk-pw"
        assert unlocked.credentials.user_password == "user-pw"
