from arch_installer.config.value_precedence import (
    INHERITANCE_ORDER,
    SettingValue,
    ValueSource,
    apply_tui_choices,
    inherit_setting_values,
    plain_values,
)


def inherited_from(environment: dict, config_file: dict) -> dict[str, SettingValue]:
    return inherit_setting_values(
        {ValueSource.ENVIRONMENT: environment, ValueSource.CONFIG_FILE: config_file}
    )


class TestInheritance:
    def test_environment_beats_config_file(self):
        inherited = inherited_from(
            {"storage.target_disk": "/dev/nvme0n1"}, {"storage.target_disk": "/dev/sda"}
        )

        assert inherited["storage.target_disk"] == SettingValue(
            "/dev/nvme0n1", ValueSource.ENVIRONMENT
        )

    def test_config_file_provides_what_the_environment_does_not(self):
        inherited = inherited_from({}, {"system.hostname": "archbox"})

        assert inherited["system.hostname"] == SettingValue("archbox", ValueSource.CONFIG_FILE)

    def test_a_setting_no_source_has_stays_missing(self):
        assert "system.hostname" not in inherited_from({}, {})

    def test_the_order_is_environment_then_config_file(self):
        assert INHERITANCE_ORDER == (ValueSource.ENVIRONMENT, ValueSource.CONFIG_FILE)


class TestTuiChoices:
    def test_tui_choice_replaces_the_inherited_value(self):
        inherited = inherited_from({"docker.enabled": True}, {})

        final = apply_tui_choices(inherited, {"docker.enabled": False})

        assert final["docker.enabled"] == SettingValue(False, ValueSource.TUI)

    def test_keeping_the_inherited_value_keeps_its_source(self):
        inherited = inherited_from({}, {"system.hostname": "archbox"})

        final = apply_tui_choices(inherited, {"system.hostname": "archbox"})

        assert final["system.hostname"].source == ValueSource.CONFIG_FILE

    def test_tui_fills_a_setting_no_source_had(self):
        final = apply_tui_choices({}, {"storage.target_disk": "/dev/vda"})

        assert final["storage.target_disk"] == SettingValue("/dev/vda", ValueSource.TUI)

    def test_plain_values_drop_the_sources(self):
        final = apply_tui_choices(inherited_from({"a.b": 1}, {}), {"c.d": 2})

        assert plain_values(final) == {"a.b": 1, "c.d": 2}
