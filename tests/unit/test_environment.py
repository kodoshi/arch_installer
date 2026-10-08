import pytest

from arch_installer.config.config_file import config_file_setting_values, read_config_file
from arch_installer.config.environment import (
    ENVIRONMENT_SETTINGS,
    Environment,
    EnvVariable,
    variable_for_setting,
)
from arch_installer.config.installer_config_builder import CREDENTIAL_PATHS
from arch_installer.config.models import CpuVendor, Desktop, GpuVendor, WipeMethod
from arch_installer.errors import ConfigurationError
from tests.unit.conftest import UNIT_CONFIG_PATH


def environment(**variables: str) -> Environment:
    return Environment(variables)


class TestEnvironmentSettingValues:
    def test_only_variables_that_are_set_provide_values(self):
        values = environment(TARGET_DISK="/dev/nvme0n1", VERBOSE="true").setting_values()

        assert values == {"storage.target_disk": "/dev/nvme0n1"}

    def test_empty_variable_provides_nothing(self):
        assert environment(TARGET_DISK="  ").setting_values() == {}

    def test_choices_read_as_their_enum(self):
        values = environment(
            WIPE_METHOD="secure", CPU_VENDOR="intel", GPU_VENDOR="NVIDIA"
        ).setting_values()

        assert values["storage.wipe_method"] == WipeMethod.SECURE
        assert values["system.cpu_vendor"] == CpuVendor.INTEL
        assert values["gpu.vendor"] == GpuVendor.NVIDIA

    def test_lists_read_as_tuples(self):
        values = environment(SELECTED_DESKTOPS="gnome, kde").setting_values()

        assert values["packages.selected_desktops"] == (Desktop.GNOME, Desktop.KDE)

    def test_enable_swap_false_turns_swap_off(self):
        values = environment(ENABLE_SWAP="false").setting_values()

        assert values["storage.swap.enabled"] is False

    def test_numbers_read_as_integers(self):
        assert environment(SWAP_SIZE_MB="2048").setting_values()["storage.swap.size_mb"] == 2048

    def test_invalid_choice_names_the_variable(self):
        with pytest.raises(ConfigurationError, match="GPU_VENDOR"):
            environment(GPU_VENDOR="banana").setting_values()

    def test_invalid_flag_names_the_variable(self):
        with pytest.raises(ConfigurationError, match="ENABLE_DOCKER"):
            environment(ENABLE_DOCKER="maybe").setting_values()

    def test_passwords_are_credential_settings(self):
        values = environment(LUKS_PASSWORD="disk", USER_PASSWORD="user").setting_values()

        assert values["credentials.luks_password"] == "disk"
        assert values["credentials.user_password"] == "user"


class TestEnvironmentSettingTable:
    def test_every_variable_names_a_setting_the_model_declares(self):
        known_paths = set(config_file_setting_values(read_config_file(UNIT_CONFIG_PATH)))
        known_paths |= set(CREDENTIAL_PATHS)

        unknown = [
            setting.setting_path
            for setting in ENVIRONMENT_SETTINGS
            if setting.setting_path not in known_paths
        ]

        assert unknown == []

    def test_setting_paths_lead_back_to_their_variable(self):
        assert variable_for_setting("storage.target_disk") == EnvVariable.TARGET_DISK
        assert variable_for_setting("storage.luks.cipher") is None


class TestSwitches:
    def test_unset_switch_is_off(self):
        assert not environment().switch_is_on(EnvVariable.NON_INTERACTIVE)

    def test_switch_reads_true_words(self):
        assert environment(NON_INTERACTIVE="yes").switch_is_on(EnvVariable.NON_INTERACTIVE)
