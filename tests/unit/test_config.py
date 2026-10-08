import dataclasses

import pytest

from arch_installer.config.config_file import (
    config_file_setting_values,
    decrypted_credentials,
    load_config_file,
    read_config_file,
)
from arch_installer.config.installer_config_builder import (
    MissingSettingsError,
    build_installer_config,
)
from arch_installer.config.models import (
    LUKS_PASSWORD_SECRET,
    USER_PASSWORD_SECRET,
    Desktop,
    WipeMethod,
)
from arch_installer.core.secrets import encrypt_secret
from arch_installer.errors import ConfigurationError
from tests.unit.conftest import UNIT_CONFIG_PATH


def unit_raw() -> dict:
    # a fresh, complete config.yaml mapping each test may change
    return read_config_file(UNIT_CONFIG_PATH)


def build_from(raw: dict):
    return build_installer_config(config_file_setting_values(raw))


class TestConfigFile:
    def test_missing_file_is_reported(self, tmp_path):
        with pytest.raises(ConfigurationError, match="not found"):
            load_config_file(tmp_path / "nope.yaml")

    def test_malformed_yaml_is_reported(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("invalid: yaml: [")

        with pytest.raises(ConfigurationError, match="Failed to parse"):
            load_config_file(path)

    def test_unknown_key_is_refused_with_its_path(self):
        raw = unit_raw()
        raw["storage"]["bogus"] = 1

        with pytest.raises(ConfigurationError, match="storage: bogus"):
            config_file_setting_values(raw)

    def test_plain_text_credentials_are_refused(self):
        raw = unit_raw()
        raw["credentials"] = {"luks_password": "in-clear"}

        with pytest.raises(ConfigurationError, match=r"Unknown configuration key.*credentials"):
            config_file_setting_values(raw)

    def test_sections_flatten_to_dotted_paths_and_lists_stay_whole(self):
        values = config_file_setting_values(unit_raw())

        assert values["storage.swap.size_mb"] == 1024
        assert [item["name"] for item in values["storage.btrfs.subvolumes"]] == ["@", "@home"]
        assert values["snapper.root"]["subvolume"] == "/"

    def test_encrypted_passwords_decrypt_into_credentials(self):
        values = {
            "secrets.luks_password_encrypted": encrypt_secret("disk-pw", "k", LUKS_PASSWORD_SECRET),
            "secrets.user_password_encrypted": encrypt_secret("user-pw", "k", USER_PASSWORD_SECRET),
        }

        credentials = decrypted_credentials(values, "k")

        assert credentials == {
            "credentials.luks_password": "disk-pw",
            "credentials.user_password": "user-pw",
        }


class TestInstallerConfigBuilder:
    def test_every_missing_setting_is_reported_together_by_path(self):
        raw = unit_raw()
        del raw["docker"]
        del raw["storage"]["swap"]["size_mb"]
        del raw["storage"]["wipe_method"]

        with pytest.raises(MissingSettingsError) as raised:
            build_from(raw)

        assert raised.value.setting_paths == [
            "storage.wipe_method",
            "storage.swap.size_mb",
            "docker",
        ]

    def test_a_missing_setting_is_never_filled_in_by_code(self):
        raw = unit_raw()
        del raw["firewall"]["enabled"]

        with pytest.raises(MissingSettingsError, match=r"firewall\.enabled"):
            build_from(raw)

    def test_unknown_setting_path_is_refused(self):
        values = config_file_setting_values(unit_raw())
        values["storage.swap.colour"] = "blue"

        with pytest.raises(ConfigurationError, match="swap: colour"):
            build_installer_config(values)

    def test_unquoted_yaml_boolean_for_a_text_setting_is_refused(self):
        raw = unit_raw()
        raw["boot"]["cmdline"]["hardening"]["pti"] = True

        with pytest.raises(ConfigurationError, match="must be a string"):
            build_from(raw)

    def test_choices_parse_from_their_text(self):
        raw = unit_raw()
        raw["storage"]["wipe_method"] = "secure"
        raw["packages"]["selected_desktops"] = ["gnome", "kde"]

        config = build_from(raw)

        assert config.storage.wipe_method == WipeMethod.SECURE
        assert config.packages.selected_desktops == (Desktop.GNOME, Desktop.KDE)

    def test_passwords_no_source_provided_stay_empty_for_validation_to_judge(self):
        config = build_from(unit_raw())

        assert config.credentials.luks_password == ""
        assert config.credentials.source_luks_password == ""


class TestExampleConfig:
    def test_shipped_config_is_complete_once_given_a_target_disk(self, example_config):
        assert example_config.system.hostname
        assert example_config.packages.selected_desktops

    def test_shipped_config_leaves_the_target_disk_to_the_environment_or_tui(self):
        with pytest.raises(MissingSettingsError) as raised:
            load_config_file(UNIT_CONFIG_PATH.parent.parent.parent / "config" / "config.yaml")

        assert raised.value.setting_paths == ["storage.target_disk"]

    def test_config_is_immutable(self, example_config):
        with pytest.raises(dataclasses.FrozenInstanceError):
            example_config.system.hostname = "changed"
