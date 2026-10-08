import pytest
import yaml

from arch_installer.cli import assemble_installer_config
from arch_installer.config.environment import Environment
from arch_installer.config.models import LUKS_PASSWORD_SECRET, USER_PASSWORD_SECRET
from arch_installer.config.value_precedence import SettingValue, ValueSource
from arch_installer.core.secrets import encrypt_secret
from arch_installer.errors import ConfigurationError
from tests.unit.conftest import UNIT_CONFIG_PATH


@pytest.fixture
def config_path(tmp_path):
    def written(change=lambda raw: None):
        raw = yaml.safe_load(UNIT_CONFIG_PATH.read_text())
        change(raw)
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(raw))
        return path

    return written


def environment_for(path, **variables: str) -> Environment:
    return Environment({"CONFIG_PATH": str(path), **variables})


# stands in for the curses TUI: records what it was shown, answers with fixed choices
class ScriptedTui:
    def __init__(self, choices: dict) -> None:
        self.choices = choices
        self.shown: dict[str, SettingValue] = {}

    def __call__(self, inherited):
        self.shown = dict(inherited)
        return self.choices


class TestNonInteractive:
    def test_environment_variable_beats_config_file(self, config_path):
        environment = environment_for(config_path(), TARGET_DISK="/dev/nvme0n1")

        config = assemble_installer_config(environment, tui=None)

        assert config.storage.target_disk == "/dev/nvme0n1"

    def test_config_file_value_is_used_when_the_environment_has_none(self, config_path):
        config = assemble_installer_config(environment_for(config_path()), tui=None)

        assert config.storage.target_disk == "/dev/loop0"

    def test_missing_setting_names_every_source_that_could_provide_it(self, config_path):
        def without_target_disk(raw):
            del raw["storage"]["target_disk"]

        environment = environment_for(config_path(without_target_disk))

        with pytest.raises(
            ConfigurationError, match=r"storage\.target_disk \(config\.yaml or TARGET_DISK\)"
        ):
            assemble_installer_config(environment, tui=None)


class TestInteractive:
    def test_tui_is_shown_each_inherited_value_with_its_source(self, config_path):
        tui = ScriptedTui({})
        environment = environment_for(config_path(), TARGET_DISK="/dev/nvme0n1")

        assemble_installer_config(environment, tui)

        assert tui.shown["storage.target_disk"] == SettingValue(
            "/dev/nvme0n1", ValueSource.ENVIRONMENT
        )
        assert tui.shown["system.hostname"] == SettingValue("testhost", ValueSource.CONFIG_FILE)

    def test_tui_choice_has_the_last_word(self, config_path):
        tui = ScriptedTui({"storage.target_disk": "/dev/sdb", "docker.enabled": True})
        environment = environment_for(config_path(), TARGET_DISK="/dev/nvme0n1")

        config = assemble_installer_config(environment, tui)

        assert config.storage.target_disk == "/dev/sdb"
        assert config.docker.enabled is True

    def test_tui_fills_a_setting_no_source_provides(self, config_path):
        def without_target_disk(raw):
            del raw["storage"]["target_disk"]

        tui = ScriptedTui({"storage.target_disk": "/dev/vda"})

        config = assemble_installer_config(environment_for(config_path(without_target_disk)), tui)

        assert config.storage.target_disk == "/dev/vda"


class TestEncryptedPasswords:
    def with_encrypted_passwords(self, key: str):
        def change(raw):
            raw["secrets"] = {
                "luks_password_encrypted": encrypt_secret("disk-pw", key, LUKS_PASSWORD_SECRET),
                "user_password_encrypted": encrypt_secret("user-pw", key, USER_PASSWORD_SECRET),
            }

        return change

    def test_passwords_unlock_with_the_key_from_the_environment(self, config_path):
        path = config_path(self.with_encrypted_passwords("key"))

        config = assemble_installer_config(
            environment_for(path, ARCH_INSTALLER_SECRETS_KEY="key"), tui=None
        )

        assert config.credentials.luks_password == "disk-pw"
        assert config.credentials.user_password == "user-pw"

    def test_passwords_from_the_environment_need_no_key(self, config_path):
        path = config_path(self.with_encrypted_passwords("unknown key"))
        environment = environment_for(path, LUKS_PASSWORD="env-disk", USER_PASSWORD="env-user")

        config = assemble_installer_config(environment, tui=None)

        assert config.credentials.luks_password == "env-disk"
