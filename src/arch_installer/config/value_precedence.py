"""Where each setting's value comes from, and which source wins.

non-interactive:  environment variables > config.yaml
interactive:      environment variables > config.yaml, then the TUI may replace the value
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ValueSource(StrEnum):
    ENVIRONMENT = "environment"
    CONFIG_FILE = "config.yaml"
    TUI = "TUI"


@dataclass(frozen=True)
class SettingValue:
    value: Any
    source: ValueSource


# the first source in this order that has a value for a setting gives the inherited value
INHERITANCE_ORDER = (ValueSource.ENVIRONMENT, ValueSource.CONFIG_FILE)


def inherit_setting_values(
    values_by_source: Mapping[ValueSource, Mapping[str, Any]],
) -> dict[str, SettingValue]:
    inherited: dict[str, SettingValue] = {}
    for source in INHERITANCE_ORDER:
        for setting_path, value in values_by_source[source].items():
            inherited.setdefault(setting_path, SettingValue(value, source))
    return inherited


# the TUI has the last word; a choice equal to the inherited value keeps its source
def apply_tui_choices(
    inherited: Mapping[str, SettingValue], tui_choices: Mapping[str, Any]
) -> dict[str, SettingValue]:
    chosen = dict(inherited)
    for setting_path, value in tui_choices.items():
        previous = inherited.get(setting_path)
        if previous is None or previous.value != value:
            chosen[setting_path] = SettingValue(value, ValueSource.TUI)
    return chosen


def plain_values(setting_values: Mapping[str, SettingValue]) -> dict[str, Any]:
    return {setting_path: setting.value for setting_path, setting in setting_values.items()}
