"""Interactive setup: asks the install steps' questions, in step order, through a
SetupFrontend.
"""

from collections.abc import Mapping
from typing import Any

from arch_installer.config.value_precedence import SettingValue, apply_tui_choices
from arch_installer.install_steps.questions import (
    Choice,
    ChooseMany,
    ChooseOne,
    EnterSecret,
    EnterText,
    MachineFacts,
    Question,
    Switch,
)
from arch_installer.install_steps.registry import INSTALL_STEPS
from arch_installer.install_steps.wiring import StepSetting
from arch_installer.setup.frontend import Inherited, SetupFrontend, SummaryRow


def run_setup(
    inherited: Mapping[str, SettingValue], frontend: SetupFrontend, machine: MachineFacts
) -> dict[str, Any]:
    session = SetupSession(inherited, frontend, machine)
    for step, wiring in INSTALL_STEPS.items():
        for setting in wiring.settings:
            if setting.question is not None and setting.asked_when(session.value):
                session.ask(str(step), setting)
    if not frontend.review(session.summary()):
        raise KeyboardInterrupt("installation cancelled by user")
    return session.answers


class SetupSession:
    def __init__(
        self,
        inherited: Mapping[str, SettingValue],
        frontend: SetupFrontend,
        machine: MachineFacts,
    ) -> None:
        self._inherited = inherited
        self._frontend = frontend
        self._machine = machine
        self._asked: list[tuple[str, StepSetting]] = []
        self._offered_choices: dict[str, tuple[Choice, ...]] = {}
        self.answers: dict[str, Any] = {}

    # the current value of any setting: the answer if it was asked, else the inherited one
    def value(self, setting_path: str) -> Any:
        if setting_path in self.answers:
            return self.answers[setting_path]
        inherited = self._inherited.get(setting_path)
        return inherited.value if inherited else None

    def ask(self, step: str, setting: StepSetting) -> None:
        self.answers[setting.path] = self._answer(step, setting)
        self._asked.append((step, setting))

    def _answer(self, step: str, setting: StepSetting) -> Any:
        inherited = self._inherited.get(setting.path)
        match setting.question:
            case Switch() as question:
                switch_state = _inherited_as(inherited, bool)
                return self._frontend.switch(step, question, switch_state)
            case ChooseOne() as question:
                return self._choose_one(step, setting.path, question, inherited)
            case ChooseMany() as question:
                return self._choose_many(step, question, inherited)
            case EnterText() as question:
                return self._frontend.enter_text(step, question, _inherited_text(inherited))
            case EnterSecret() as question:
                return self._secret(step, question, inherited)
        raise TypeError(f"{setting.path} has no question to ask")

    def _choose_one(
        self, step: str, setting_path: str, question: ChooseOne, inherited: SettingValue | None
    ) -> Any:
        choices = question.choices
        if not isinstance(choices, tuple):
            choices = choices(inherited.value if inherited else None, self._machine)
        if not choices:
            typed = EnterText(question.label, question.typed_prompt)
            return self._frontend.enter_text(step, typed, _inherited_text(inherited))
        self._offered_choices[setting_path] = choices
        return self._frontend.choose_one(step, question, choices, _inherited_as(inherited, None))

    def _choose_many(
        self, step: str, question: ChooseMany, inherited: SettingValue | None
    ) -> list[Any]:
        chosen = self._frontend.choose_many(step, question, _inherited_as(inherited, list))
        # a selection is a set: the same items in another order keep the inherited value
        if inherited is not None and _as_texts(chosen) == _as_texts(inherited.value):
            return inherited.value
        return chosen

    def _secret(self, step: str, question: EnterSecret, inherited: SettingValue | None) -> str:
        has_inherited = inherited is not None and bool(inherited.value)
        if has_inherited and self._frontend.keep_secret(step, question, str(inherited.source)):
            return inherited.value
        return self._frontend.enter_secret(step, question)

    def summary(self) -> list[SummaryRow]:
        final = apply_tui_choices(self._inherited, self.answers)
        return [
            SummaryRow(
                step,
                setting.question.label,
                self._shown(setting, final[setting.path].value),
                str(final[setting.path].source),
            )
            for step, setting in self._asked
            if setting.question is not None
        ]

    def _shown(self, setting: StepSetting, value: Any) -> str:
        question: Question | None = setting.question
        match question:
            case EnterSecret():
                return "set" if value else "not set"
            case Switch():
                return question.on_label if value else question.off_label
            case ChooseMany():
                return ", ".join(str(item) for item in value) or "none"
            case ChooseOne():
                offered = self._offered_choices.get(setting.path, ())
                return next(
                    (choice.label for choice in offered if choice.value == value), str(value)
                )
        return str(value)


def _inherited_as(setting_value: SettingValue | None, convert: type | None) -> Inherited | None:
    if setting_value is None or setting_value.value is None:
        return None
    value = convert(setting_value.value) if convert is not None else setting_value.value
    return Inherited(value, str(setting_value.source))


# an empty text (no USB device, the vendor's own GPU driver) is no value to offer
def _inherited_text(setting_value: SettingValue | None) -> Inherited[str] | None:
    if setting_value is None or setting_value.value in (None, ""):
        return None
    return Inherited(str(setting_value.value), str(setting_value.source))


# YAML gives plain strings, environment variables give enums: both compare as text
def _as_texts(values: Any) -> set[str]:
    return {str(value) for value in values}
