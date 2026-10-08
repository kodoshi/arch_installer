"""Question types of the interactive setup, independent of any front-end."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Choice:
    value: Any
    label: str


@dataclass(frozen=True)
class Disk:
    path: str
    model: str
    size: str


# facts about the machine that some questions offer as choices
class MachineFacts(Protocol):
    def disks(self) -> tuple[Disk, ...]: ...


# choices computed when the question is asked, from the inherited value and the machine
ChoiceSource = Callable[[Any, MachineFacts], tuple[Choice, ...]]


@dataclass(frozen=True)
class ChooseOne:
    label: str
    choices: tuple[Choice, ...] | ChoiceSource
    description: str = ""
    # asked as text instead when no choice is available, e.g. no disk was detected
    typed_prompt: str = ""


@dataclass(frozen=True)
class ChooseMany:
    label: str
    choices: tuple[Choice, ...]
    description: str = ""


@dataclass(frozen=True)
class EnterText:
    label: str
    prompt: str


@dataclass(frozen=True)
class EnterSecret:
    label: str
    prompt: str
    # a new password is typed twice; an existing one (to unlock a disk) once
    confirm: bool


@dataclass(frozen=True)
class Switch:
    label: str
    on_label: str = "On"
    off_label: str = "Off"
    description: str = ""


Question = ChooseOne | ChooseMany | EnterText | EnterSecret | Switch
