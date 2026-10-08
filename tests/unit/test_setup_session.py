from typing import Any

import pytest

from arch_installer.config.config_file import config_file_setting_values, read_config_file
from arch_installer.config.models import GpuVendor
from arch_installer.config.value_precedence import (
    SettingValue,
    ValueSource,
    inherit_setting_values,
)
from arch_installer.install_steps.questions import (
    Choice,
    ChooseMany,
    ChooseOne,
    Disk,
    EnterSecret,
    EnterText,
    Switch,
)
from arch_installer.setup.frontend import Inherited, SetupFrontend, SummaryRow
from arch_installer.setup.session import run_setup
from tests.unit.conftest import UNIT_CONFIG_PATH


# stands in for a real front-end: answers by question label, and for any question it has
# no answer for it keeps the inherited value, the way pressing Enter does
class ScriptedFrontend(SetupFrontend):
    def __init__(self, answers: dict[str, Any], confirm: bool = True) -> None:
        self._answers = answers
        self._confirm = confirm
        self.asked: list[tuple[str, str]] = []
        self.inherited_shown: dict[str, Inherited | None] = {}
        self.reviewed: list[SummaryRow] = []

    def _record(self, step: str, label: str, inherited: Inherited | None) -> None:
        self.asked.append((step, label))
        self.inherited_shown[label] = inherited

    def switch(self, step: str, question: Switch, inherited: Inherited[bool] | None) -> bool:
        self._record(step, question.label, inherited)
        return self._answers.get(question.label, inherited.value if inherited else False)

    def choose_one(
        self,
        step: str,
        question: ChooseOne,
        choices: tuple[Choice, ...],
        inherited: Inherited[Any] | None,
    ) -> Any:
        self._record(step, question.label, inherited)
        if question.label in self._answers:
            return self._answers[question.label]
        return inherited.value if inherited else choices[0].value

    def choose_many(
        self, step: str, question: ChooseMany, inherited: Inherited[list[Any]] | None
    ) -> list[Any]:
        self._record(step, question.label, inherited)
        return self._answers.get(question.label, inherited.value if inherited else [])

    def enter_text(self, step: str, question: EnterText, inherited: Inherited[str] | None) -> str:
        self._record(step, question.label, inherited)
        return self._answers.get(question.label, inherited.value if inherited else "")

    def keep_secret(self, step: str, question: EnterSecret, source: str) -> bool:
        self._record(step, question.label, Inherited("(hidden)", source))
        return question.label not in self._answers

    def enter_secret(self, step: str, question: EnterSecret) -> str:
        self._record(step, question.label, None)
        return self._answers[question.label]

    def review(self, rows: list[SummaryRow]) -> bool:
        self.reviewed = rows
        return self._confirm


class TwoDisks:
    def disks(self) -> tuple[Disk, ...]:
        return (Disk("/dev/vda", "QEMU", "20G"), Disk("/dev/vdb", "QEMU", "4G"))


class NoDisks:
    def disks(self) -> tuple[Disk, ...]:
        return ()


def inherited_values(**environment_values: Any) -> dict[str, SettingValue]:
    config_file_values = config_file_setting_values(read_config_file(UNIT_CONFIG_PATH))
    return inherit_setting_values(
        {ValueSource.ENVIRONMENT: environment_values, ValueSource.CONFIG_FILE: config_file_values}
    )


PASSWORDS = {"LUKS password": "disk-secret", "User password": "user-secret"}


class TestQuestionOrderAndConditions:
    def test_questions_follow_the_install_steps(self):
        frontend = ScriptedFrontend(PASSWORDS)

        run_setup(inherited_values(), frontend, TwoDisks())

        steps_in_order = list(dict.fromkeys(step for step, _ in frontend.asked))
        assert steps_in_order[:4] == ["Migration staging", "Storage", "Packages", "System"]
        assert steps_in_order[-1] == "Firewall"

    def test_nvidia_driver_is_asked_only_for_an_nvidia_card(self):
        without_nvidia = ScriptedFrontend(PASSWORDS)
        with_nvidia = ScriptedFrontend({**PASSWORDS, "GPU vendor": GpuVendor.NVIDIA})

        run_setup(inherited_values(), without_nvidia, TwoDisks())
        run_setup(inherited_values(), with_nvidia, TwoDisks())

        assert ("Packages", "NVIDIA driver") not in without_nvidia.asked
        assert ("Packages", "NVIDIA driver") in with_nvidia.asked

    def test_turning_swap_off_skips_its_size_and_hibernation(self):
        frontend = ScriptedFrontend({**PASSWORDS, "Swap file": False})

        answers = run_setup(inherited_values(), frontend, TwoDisks())

        labels = [label for _, label in frontend.asked]
        assert "Swap size" not in labels and "Hibernation" not in labels
        assert answers["storage.swap.enabled"] is False

    def test_old_disk_password_is_asked_only_when_migrating(self):
        frontend = ScriptedFrontend(
            {**PASSWORDS, "Installation type": True, "Old disk password": "old"}
        )

        answers = run_setup(inherited_values(), frontend, TwoDisks())

        assert answers["credentials.source_luks_password"] == "old"


class TestInheritedValues:
    def test_each_question_is_shown_its_inherited_value_and_source(self):
        frontend = ScriptedFrontend(PASSWORDS)

        run_setup(inherited_values(), frontend, TwoDisks())

        assert frontend.inherited_shown["Hostname"] == Inherited("testhost", "config.yaml")

    def test_an_environment_value_is_shown_as_coming_from_the_environment(self):
        frontend = ScriptedFrontend(PASSWORDS)

        run_setup(inherited_values(**{"storage.target_disk": "/dev/vdb"}), frontend, TwoDisks())

        assert frontend.inherited_shown["Disk"] == Inherited("/dev/vdb", "environment")

    def test_an_inherited_password_can_be_kept_without_being_shown(self):
        frontend = ScriptedFrontend({"User password": "user-secret"})

        answers = run_setup(
            inherited_values(**{"credentials.luks_password": "from-env"}), frontend, TwoDisks()
        )

        assert answers["credentials.luks_password"] == "from-env"
        assert frontend.inherited_shown["LUKS password"] == Inherited("(hidden)", "environment")

    def test_the_same_desktops_in_another_order_keep_the_inherited_value(self):
        inherited = inherited_values(**{"packages.selected_desktops": ("kde", "gnome")})
        frontend = ScriptedFrontend({**PASSWORDS, "Desktops": ["gnome", "kde"]})

        answers = run_setup(inherited, frontend, TwoDisks())

        assert answers["packages.selected_desktops"] == ("kde", "gnome")


class TestDiskQuestion:
    def test_without_detected_disks_the_disk_is_typed(self):
        frontend = ScriptedFrontend({**PASSWORDS, "Disk": "/dev/nvme0n1"})

        answers = run_setup(inherited_values(), frontend, NoDisks())

        assert answers["storage.target_disk"] == "/dev/nvme0n1"


class TestSummary:
    def test_summary_lists_every_answer_with_its_source_and_hides_passwords(self):
        frontend = ScriptedFrontend({**PASSWORDS, "Hostname": "archbox"})

        run_setup(inherited_values(), frontend, TwoDisks())

        rows = {row.label: row for row in frontend.reviewed}
        assert rows["Hostname"] == SummaryRow("System", "Hostname", "archbox", "TUI")
        assert rows["Username"].source == "config.yaml"
        assert rows["LUKS password"].value == "set"

    def test_declining_the_summary_cancels(self):
        frontend = ScriptedFrontend(PASSWORDS, confirm=False)

        with pytest.raises(KeyboardInterrupt):
            run_setup(inherited_values(), frontend, TwoDisks())
