from collections import Counter
from dataclasses import fields

from arch_installer.config.environment import EnvVariable
from arch_installer.config.installer_config_builder import setting_type
from arch_installer.config.models import InstallerConfig
from arch_installer.install_steps.registry import (
    INSTALL_STEPS,
    InstallStep,
    all_settings,
    swap_size_choices,
    variable_for_setting,
)

# not configured per step: passwords are settings of their steps, the encrypted copies in
# config.yaml are unlocked before any step runs
NOT_STEP_SECTIONS = {"credentials", "secrets"}


def claims() -> list[str]:
    claimed = [section for wiring in INSTALL_STEPS.values() for section in wiring.config_sections]
    return claimed + [setting.path for setting in all_settings()]


class TestRegistryCompleteness:
    def test_every_setting_and_section_is_a_path_the_model_declares(self):
        for path in claims():
            setting_type(path)

    def test_each_variable_provides_exactly_one_setting(self):
        counts = Counter(
            setting.environment_variable
            for setting in all_settings()
            if setting.environment_variable is not None
        )

        assert all(count == 1 for count in counts.values()), counts

    def test_every_setting_variable_is_in_the_registry(self):
        tool_variables = {
            EnvVariable.CONFIG_PATH,
            EnvVariable.NON_INTERACTIVE,
            EnvVariable.VERBOSE,
            EnvVariable.NO_WRITE,
            EnvVariable.SECRETS_KEY,
        }
        registered = {setting.environment_variable for setting in all_settings()}

        assert set(EnvVariable) - tool_variables <= registered

    def test_every_config_section_belongs_to_a_step(self):
        top_level_claims = {path.split(".")[0] for path in claims()}
        sections = {field.name for field in fields(InstallerConfig)} - NOT_STEP_SECTIONS

        assert sections <= top_level_claims


class TestRegistryContents:
    def test_steps_run_in_installation_order(self):
        assert list(INSTALL_STEPS)[:4] == [
            InstallStep.MIGRATION_STAGING,
            InstallStep.STORAGE,
            InstallStep.PACMAN_MIRRORS,
            InstallStep.PACKAGES,
        ]
        assert list(INSTALL_STEPS)[-1] == InstallStep.FIREWALL

    def test_a_setting_path_leads_back_to_its_variable(self):
        assert variable_for_setting("storage.target_disk") == EnvVariable.TARGET_DISK
        assert variable_for_setting("storage.luks.cipher") is None

    def test_inherited_swap_size_is_offered_in_size_order(self):
        choices = swap_size_choices(1024, machine=None)

        assert [choice.value for choice in choices][:3] == [1024, 4096, 8192]
        assert choices[0].label == "1 GB"

    def test_inherited_swap_preset_is_not_offered_twice(self):
        values = [choice.value for choice in swap_size_choices(8192, machine=None)]

        assert values.count(8192) == 1

    def test_swap_size_that_is_not_whole_gigabytes_shows_megabytes(self):
        labels = [choice.label for choice in swap_size_choices(1536, machine=None)]

        assert "1536 MB" in labels
