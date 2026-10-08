"""Types the install step registry is built from: step wiring, settings and conditions."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from arch_installer.config.environment import EnvVariable
from arch_installer.config.models import InstallerConfig
from arch_installer.executors.base import StepExecutor
from arch_installer.install_steps.questions import Question

# setting path -> its current value
SettingLookup = Callable[[str], Any]
StepCondition = Callable[[SettingLookup], bool]


def always(value: SettingLookup) -> bool:
    return True


def when(setting_path: str) -> StepCondition:
    return lambda value: bool(value(setting_path))


def when_equal(setting_path: str, expected: Any) -> StepCondition:
    return lambda value: value(setting_path) == expected


def config_lookup(config: InstallerConfig) -> SettingLookup:
    def value(setting_path: str) -> Any:
        current: Any = config
        for name in setting_path.split("."):
            current = getattr(current, name)
        return current

    return value


# one value: its config.yaml key, the variable that can provide it, the question that asks it
@dataclass(frozen=True)
class StepSetting:
    path: str
    environment_variable: EnvVariable | None
    question: Question | None
    asked_when: StepCondition = always


@dataclass(frozen=True)
class StepWiring:
    # config.yaml sections that configure this step and have no variable or question
    config_sections: tuple[str, ...]
    settings: tuple[StepSetting, ...]
    enabled: StepCondition
    executor: type[StepExecutor]
