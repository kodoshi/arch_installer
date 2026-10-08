"""Curses front-end of the interactive setup."""

import curses
from typing import Any

from arch_installer.config.value_precedence import SettingValue
from arch_installer.core.command import CommandRunner, SystemCommandRunner
from arch_installer.install_steps.questions import (
    Choice,
    ChooseMany,
    ChooseOne,
    EnterSecret,
    EnterText,
    Switch,
)
from arch_installer.setup.frontend import Inherited, SetupFrontend, SummaryRow
from arch_installer.setup.machine import SystemMachineFacts
from arch_installer.setup.session import run_setup
from arch_installer.tui.widgets import (
    MenuOption,
    checkbox_menu,
    confirm_screen,
    info_screen,
    init_colors,
    password_input_with_confirm,
    radio_menu,
    text_input,
)

WELCOME_TEXT = (
    "Interactive setup.\n\n"
    "Each screen starts on the value inherited from the environment or\n"
    "config.yaml and shows where it came from. Press Enter to keep it,\n"
    "or choose another.\n\n"
    "  Arrows  navigate      Enter  confirm\n"
    "  Space   toggle        Ctrl+C quit"
)
KEEP_SECRET = "keep"
SECRET_OPTIONS = [
    MenuOption(KEEP_SECRET, "Keep the inherited password"),
    MenuOption("replace", "Enter a new password"),
]


def _title(step: str, label: str) -> str:
    return f"{step}: {label}"


# menus pick by position, so values of any type (enums, numbers) work the same way
def _options(choices: tuple[Choice, ...]) -> list[MenuOption]:
    return [MenuOption(str(position), choice.label) for position, choice in enumerate(choices)]


def _positions_of(choices: tuple[Choice, ...], values: list[Any]) -> list[str]:
    return [str(position) for position, choice in enumerate(choices) if choice.value in values]


class CursesFrontend(SetupFrontend):
    def __init__(self, window: curses.window) -> None:
        self._window = window

    def switch(self, step: str, question: Switch, inherited: Inherited[bool] | None) -> bool:
        options = [MenuOption("off", question.off_label), MenuOption("on", question.on_label)]
        shown = (
            Inherited("on" if inherited.value else "off", inherited.source) if inherited else None
        )
        title = _title(step, question.label)
        return radio_menu(self._window, title, options, shown, question.description) == "on"

    def choose_one(
        self,
        step: str,
        question: ChooseOne,
        choices: tuple[Choice, ...],
        inherited: Inherited[Any] | None,
    ) -> Any:
        positions = _positions_of(choices, [inherited.value]) if inherited else []
        shown = Inherited(positions[0], inherited.source) if inherited and positions else None
        title = _title(step, question.label)
        chosen = radio_menu(self._window, title, _options(choices), shown, question.description)
        return choices[int(chosen)].value

    def choose_many(
        self, step: str, question: ChooseMany, inherited: Inherited[list[Any]] | None
    ) -> list[Any]:
        shown = (
            Inherited(_positions_of(question.choices, inherited.value), inherited.source)
            if inherited
            else None
        )
        title = _title(step, question.label)
        chosen = checkbox_menu(
            self._window, title, _options(question.choices), shown, question.description
        )
        return [question.choices[int(position)].value for position in chosen]

    def enter_text(self, step: str, question: EnterText, inherited: Inherited[str] | None) -> str:
        title = _title(step, question.label)
        return text_input(self._window, title, question.prompt, inherited, required=True)

    def keep_secret(self, step: str, question: EnterSecret, source: str) -> bool:
        description = f"{question.label}: inherited from {source}."
        title = _title(step, question.label)
        kept = radio_menu(self._window, title, SECRET_OPTIONS, None, description)
        return kept == KEEP_SECRET

    def enter_secret(self, step: str, question: EnterSecret) -> str:
        title = _title(step, question.label)
        if question.confirm:
            return password_input_with_confirm(self._window, title, question.prompt)
        return text_input(
            self._window, title, question.prompt, inherited=None, required=True, masked=True
        )

    def review(self, rows: list[SummaryRow]) -> bool:
        items = [(f"{row.step}.{row.label}", row.value, row.source) for row in rows]
        return confirm_screen(self._window, "Configuration Summary", items)


def _setup(
    window: curses.window, inherited: dict[str, SettingValue], runner: CommandRunner
) -> dict[str, Any]:
    init_colors()
    curses.curs_set(0)
    info_screen(window, "DALI - Declarative Arch Linux Installer", WELCOME_TEXT)
    return run_setup(inherited, CursesFrontend(window), SystemMachineFacts(runner))


# quitting raises KeyboardInterrupt, which the caller reports as a cancellation
def run_tui_setup(
    inherited: dict[str, SettingValue], runner: CommandRunner | None = None
) -> dict[str, Any]:
    return curses.wrapper(_setup, inherited, runner or SystemCommandRunner())
