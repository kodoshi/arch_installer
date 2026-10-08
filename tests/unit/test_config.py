import dataclasses

import pytest

from arch_installer.config.loader import load_config, parse_config
from arch_installer.config.models import Desktop, InstallerConfig, WipeMethod
from arch_installer.errors import ConfigurationError


def minimal_raw() -> dict:
    return {
        "system": {"hostname": "testhost", "timezone": "UTC"},
        "packages": {"base": ["base", "linux"]},
    }


class TestConfigLoader:
    def test_loads_only_declared_keys_and_defaults_the_rest(self):
        config = parse_config(minimal_raw())
        assert isinstance(config, InstallerConfig)
        assert config.system.hostname == "testhost"
        # untouched sections fall back to their model defaults
        assert config.storage.wipe_method == WipeMethod.QUICK
        assert config.firewall.enabled is True

    def test_raises_when_file_missing(self, tmp_path):
        with pytest.raises(ConfigurationError, match="not found"):
            load_config(tmp_path / "nope.yaml")

    def test_raises_when_yaml_malformed(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("invalid: yaml: [")
        with pytest.raises(ConfigurationError, match="Failed to parse"):
            load_config(path)

    def test_rejects_unknown_key_with_its_path(self):
        raw = minimal_raw()
        raw["storage"] = {"target_disk": "/dev/sda", "bogus": 1}
        with pytest.raises(ConfigurationError, match="storage: bogus"):
            parse_config(raw)

    def test_reports_missing_required_fields(self):
        with pytest.raises(ConfigurationError, match=r"system\.timezone"):
            parse_config({"system": {"hostname": "h"}, "packages": {"base": ["base"]}})

    def test_rejects_unquoted_yaml_boolean_for_string_field(self):
        raw = minimal_raw()
        raw["boot"] = {"cmdline": {"hardening": {"pti": True}}}
        with pytest.raises(ConfigurationError, match="must be a string"):
            parse_config(raw)


class TestPartialSectionMerge:
    def test_partial_section_keeps_the_sections_own_defaults(self):
        # home snapshots keep their distinct limits even when only subvolume is given
        raw = minimal_raw()
        raw["snapper"] = {"home": {"subvolume": "/home"}}
        config = parse_config(raw)
        assert config.snapper.home.number_limit == 5
        assert config.snapper.home.retention.monthly == 3

    def test_enum_values_parse_from_their_string(self):
        raw = minimal_raw()
        raw["storage"] = {"wipe_method": "secure"}
        raw["packages"]["selected_desktops"] = ["gnome", "kde"]
        config = parse_config(raw)
        assert config.storage.wipe_method == WipeMethod.SECURE
        assert config.packages.selected_desktops == (Desktop.GNOME, Desktop.KDE)


class TestExampleConfig:
    def test_shipped_config_matches_the_model(self, example_config):
        assert example_config.system.hostname
        assert example_config.packages.base
        assert example_config.gpu.vendor

    def test_config_is_immutable(self, example_config):
        with pytest.raises(dataclasses.FrozenInstanceError):
            example_config.system.hostname = "changed"
