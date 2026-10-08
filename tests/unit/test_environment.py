import pytest

from arch_installer.config.environment import Environment, EnvVariable
from arch_installer.config.models import CpuVendor, Desktop, GpuVendor, WipeMethod
from arch_installer.errors import ConfigurationError
from arch_installer.install_steps.registry import environment_variable_paths


def setting_values(**variables: str) -> dict:
    return Environment(variables).setting_values(environment_variable_paths())


def environment(**variables: str) -> Environment:
    return Environment(variables)


class TestEnvironmentSettingValues:
    def test_only_variables_that_are_set_provide_values(self):
        values = setting_values(TARGET_DISK="/dev/nvme0n1", VERBOSE="true")

        assert values == {"storage.target_disk": "/dev/nvme0n1"}

    def test_empty_variable_provides_nothing(self):
        assert setting_values(TARGET_DISK="  ") == {}

    def test_choices_read_as_their_enum(self):
        values = setting_values(WIPE_METHOD="secure", CPU_VENDOR="intel", GPU_VENDOR="NVIDIA")

        assert values["storage.wipe_method"] == WipeMethod.SECURE
        assert values["system.cpu_vendor"] == CpuVendor.INTEL
        assert values["gpu.vendor"] == GpuVendor.NVIDIA

    def test_lists_read_as_tuples(self):
        values = setting_values(SELECTED_DESKTOPS="gnome, kde")

        assert values["packages.selected_desktops"] == (Desktop.GNOME, Desktop.KDE)

    def test_enable_swap_false_turns_swap_off(self):
        values = setting_values(ENABLE_SWAP="false")

        assert values["storage.swap.enabled"] is False

    def test_numbers_read_as_integers(self):
        assert setting_values(SWAP_SIZE_MB="2048")["storage.swap.size_mb"] == 2048

    def test_invalid_choice_names_the_variable(self):
        with pytest.raises(ConfigurationError, match=r"GPU_VENDOR \(gpu\.vendor\)"):
            setting_values(GPU_VENDOR="banana")

    def test_invalid_flag_names_the_variable(self):
        with pytest.raises(ConfigurationError, match="ENABLE_DOCKER"):
            setting_values(ENABLE_DOCKER="maybe")

    def test_passwords_are_credential_settings(self):
        values = setting_values(LUKS_PASSWORD="disk", USER_PASSWORD="user")

        assert values["credentials.luks_password"] == "disk"
        assert values["credentials.user_password"] == "user"


class TestSwitches:
    def test_unset_switch_is_off(self):
        assert not environment().switch_is_on(EnvVariable.NON_INTERACTIVE)

    def test_switch_reads_true_words(self):
        assert environment(NON_INTERACTIVE="yes").switch_is_on(EnvVariable.NON_INTERACTIVE)
