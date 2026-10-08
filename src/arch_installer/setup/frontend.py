"""Port that interactive front-ends implement, one method per question kind."""

from dataclasses import dataclass
from typing import Any, Protocol

from arch_installer.install_steps.questions import (
    Choice,
    ChooseMany,
    ChooseOne,
    EnterSecret,
    EnterText,
    Switch,
)


# a value the setting already has, and the source it came from (shown next to it)
@dataclass(frozen=True)
class Inherited[ValueT]:
    value: ValueT
    source: str


@dataclass(frozen=True)
class SummaryRow:
    step: str
    label: str
    value: str
    source: str


class SetupFrontend(Protocol):
    def switch(self, step: str, question: Switch, inherited: Inherited[bool] | None) -> bool: ...

    def choose_one(
        self,
        step: str,
        question: ChooseOne,
        choices: tuple[Choice, ...],
        inherited: Inherited[Any] | None,
    ) -> Any: ...

    def choose_many(
        self, step: str, question: ChooseMany, inherited: Inherited[list[Any]] | None
    ) -> list[Any]: ...

    def enter_text(
        self, step: str, question: EnterText, inherited: Inherited[str] | None
    ) -> str: ...

    # an inherited secret is never shown: the front-end only asks whether to keep it
    def keep_secret(self, step: str, question: EnterSecret, source: str) -> bool: ...

    def enter_secret(self, step: str, question: EnterSecret) -> str: ...

    # the last screen: every answer with its source; False cancels the installation
    def review(self, rows: list[SummaryRow]) -> bool: ...
