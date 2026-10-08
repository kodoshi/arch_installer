import curses

import pytest

from arch_installer.tui.app import swap_size_options
from arch_installer.tui.widgets import (
    KEY_ESCAPE,
    KEY_SPACE,
    KEY_TAB,
    Inherited,
    MenuOption,
    TextEntry,
    Toggle,
    checkbox_menu,
    confirm_screen,
    radio_menu,
    toggle_menu,
)

KEY_ENTER = 10
KEY_BACKSPACE = 127
KEY_DOWN = curses.KEY_DOWN
KEY_UP = curses.KEY_UP
DESKTOPS = [
    MenuOption("gnome", "GNOME"),
    MenuOption("kde", "KDE Plasma"),
    MenuOption("hyprland", "Hyprland"),
]


# stands in for a curses window: records the text drawn on each row and answers getch()
# from a script of keys, so a widget runs exactly as on a terminal
class ScriptedWindow:
    def __init__(self, keys: list[int], rows: int = 24, columns: int = 100) -> None:
        self._keys = list(keys)
        self._size = (rows, columns)
        self.rows: dict[int, str] = {}

    def getmaxyx(self) -> tuple[int, int]:
        return self._size

    def erase(self) -> None:
        self.rows = {}

    def addstr(self, row: int, column: int, text: str, attributes: int = 0) -> None:
        line = self.rows.get(row, "").ljust(column)
        self.rows[row] = line[:column] + text + line[column + len(text) :]

    def addnstr(self, row: int, column: int, text: str, count: int, attributes: int = 0) -> None:
        self.addstr(row, column, text[:count], attributes)

    def attron(self, attributes: int) -> None:
        pass

    def attroff(self, attributes: int) -> None:
        pass

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        assert self._keys, "the widget asked for more keys than the test scripted"
        return self._keys.pop(0)

    def screen(self) -> str:
        return "\n".join(self.rows[row] for row in sorted(self.rows))


@pytest.fixture(autouse=True)
def _colors_without_a_terminal(monkeypatch):
    # curses only hands out color pairs after initscr(), which needs a real terminal
    monkeypatch.setattr(curses, "color_pair", lambda number: number << 8)


def type_text(entry: TextEntry, text: str) -> None:
    for character in text:
        assert entry.press(ord(character)) is None


class TestTextEntry:
    def test_enter_on_an_untouched_field_keeps_the_inherited_value(self):
        entry = TextEntry(inherited=Inherited("testmaximal", "config.yaml"), required=True)

        submitted = entry.press(KEY_ENTER)

        assert submitted == "testmaximal"

    def test_typing_replaces_the_inherited_value(self):
        entry = TextEntry(inherited=Inherited("testmaximal", "config.yaml"), required=True)

        type_text(entry, "myhost")
        submitted = entry.press(KEY_ENTER)

        assert submitted == "myhost"

    @pytest.mark.parametrize("text", ["aqua", "q", "quasar"])
    def test_the_letter_q_is_typed_like_any_other_character(self, text):
        entry = TextEntry(inherited=None, required=False)

        type_text(entry, text)
        submitted = entry.press(KEY_ENTER)

        assert submitted == text

    def test_escape_drops_the_override_and_restores_the_inherited_value(self):
        entry = TextEntry(inherited=Inherited("us", "environment"), required=True)
        type_text(entry, "fi")

        entry.press(KEY_ESCAPE)
        submitted = entry.press(KEY_ENTER)

        assert submitted == "us"

    def test_backspace_removes_the_last_typed_character(self):
        entry = TextEntry(inherited=None, required=False)
        type_text(entry, "vdab")

        entry.press(KEY_BACKSPACE)
        submitted = entry.press(KEY_ENTER)

        assert submitted == "vda"

    def test_required_field_without_value_refuses_enter_and_shows_an_error(self):
        entry = TextEntry(inherited=None, required=True)

        submitted = entry.press(KEY_ENTER)

        assert submitted is None
        assert entry.error

    def test_typing_after_a_refusal_clears_the_error(self):
        entry = TextEntry(inherited=None, required=True)
        entry.press(KEY_ENTER)

        type_text(entry, "x")

        assert not entry.error

    def test_optional_empty_field_submits_an_empty_value(self):
        entry = TextEntry(inherited=None, required=False)

        submitted = entry.press(KEY_ENTER)

        assert submitted == ""


class TestSwapSizeOptions:
    def test_inherited_size_outside_the_presets_is_offered_in_size_order(self):
        options = swap_size_options(1024)

        assert [option.value for option in options][:3] == ["1024", "4096", "8192"]
        assert options[0].label == "1 GB"

    def test_inherited_preset_is_not_duplicated(self):
        values = [option.value for option in swap_size_options(8192)]

        assert values.count("8192") == 1

    def test_no_swap_is_always_the_last_option(self):
        options = swap_size_options(0)

        assert options[-1].value == "0"
        assert [option.value for option in options].count("0") == 1

    def test_size_that_is_not_whole_gigabytes_is_labelled_in_megabytes(self):
        labels = [option.label for option in swap_size_options(1536)]

        assert "1536 MB" in labels


class TestRadioMenu:
    def test_enter_keeps_the_inherited_option(self):
        window = ScriptedWindow([KEY_ENTER])

        chosen = radio_menu(window, "Desktop", DESKTOPS, Inherited("kde", "config.yaml"))

        assert chosen == "kde"

    def test_arrows_choose_another_option(self):
        window = ScriptedWindow([KEY_DOWN, KEY_ENTER])

        chosen = radio_menu(window, "Desktop", DESKTOPS, Inherited("kde", "config.yaml"))

        assert chosen == "hyprland"

    def test_without_an_inherited_value_the_first_option_is_under_the_cursor(self):
        window = ScriptedWindow([KEY_ENTER])

        assert radio_menu(window, "Desktop", DESKTOPS, None) == "gnome"

    def test_inherited_option_shows_its_source(self):
        window = ScriptedWindow([KEY_ENTER])

        radio_menu(window, "Desktop", DESKTOPS, Inherited("kde", "environment"))

        assert "KDE Plasma  (inherited from environment)" in window.screen()

    def test_q_quits(self):
        with pytest.raises(KeyboardInterrupt):
            radio_menu(ScriptedWindow([ord("q")]), "Desktop", DESKTOPS, None)

    def test_a_long_list_scrolls_to_keep_the_cursor_visible(self):
        options = [MenuOption(str(number), f"Option {number}") for number in range(30)]
        window = ScriptedWindow([KEY_DOWN] * 25 + [KEY_ENTER], rows=16)

        chosen = radio_menu(window, "Long list", options, None)

        assert chosen == "25"
        assert "▸ Option 25" in window.screen()
        assert "Option 0 " not in window.screen()


class TestCheckboxMenu:
    def test_inherited_choices_start_ticked(self):
        window = ScriptedWindow([KEY_ENTER])

        chosen = checkbox_menu(
            window, "Desktops", DESKTOPS, Inherited(["kde", "gnome"], "config.yaml")
        )

        assert chosen == ["gnome", "kde"]

    def test_space_toggles_the_option_under_the_cursor(self):
        window = ScriptedWindow([KEY_SPACE, KEY_DOWN, KEY_DOWN, KEY_SPACE, KEY_ENTER])

        chosen = checkbox_menu(window, "Desktops", DESKTOPS, Inherited(["gnome"], "config.yaml"))

        assert chosen == ["hyprland"]


class TestToggleMenu:
    def toggles(self) -> list[Toggle]:
        return [
            Toggle("firewall", "Firewall", Inherited(True, "config.yaml")),
            Toggle("docker", "Docker", None),
        ]

    def test_tab_is_refused_while_a_toggle_is_unset(self):
        window = ScriptedWindow([KEY_TAB, KEY_DOWN, KEY_SPACE, KEY_TAB])

        chosen = toggle_menu(window, "Features", self.toggles())

        assert chosen == {"firewall": True, "docker": True}

    def test_the_refusal_names_the_unset_toggle(self):
        window = ScriptedWindow([KEY_TAB, ord("q")])

        with pytest.raises(KeyboardInterrupt):
            toggle_menu(window, "Features", self.toggles())

        assert "Set every feature first: Docker" in window.screen()

    def test_a_flipped_toggle_shows_its_inherited_state(self):
        window = ScriptedWindow([KEY_SPACE, KEY_DOWN, KEY_SPACE, ord("q")])

        with pytest.raises(KeyboardInterrupt):
            toggle_menu(window, "Features", self.toggles())

        assert "[ OFF]  Firewall  (inherited from config.yaml: ON)" in window.screen()


# forty summary rows, more than fit on one screen
SUMMARY_ITEMS = tuple((f"Group.Setting {number}", "value", "config.yaml") for number in range(40))


class TestSummaryScreen:
    def test_y_confirms(self):
        assert confirm_screen(ScriptedWindow([ord("y")]), "Summary", list(SUMMARY_ITEMS)) is True

    @pytest.mark.parametrize("key", [ord("n"), ord("q")])
    def test_n_and_q_cancel(self, key):
        assert confirm_screen(ScriptedWindow([key]), "Summary", list(SUMMARY_ITEMS)) is False

    def test_rows_sit_under_their_group_heading_with_their_source(self):
        window = ScriptedWindow([ord("y")])

        confirm_screen(window, "Summary", [("System.Hostname", "archbox", "environment")])

        assert "[System]" in window.screen()
        assert "Hostname" in window.screen() and "archbox" in window.screen()
        assert "environment" in window.screen()

    def test_arrows_scroll_and_stop_at_the_last_row(self):
        window = ScriptedWindow([KEY_DOWN] * 100 + [ord("y")], rows=20)

        confirm_screen(window, "Summary", list(SUMMARY_ITEMS))

        assert "Setting 39" in window.screen()
        assert "Setting 0 " not in window.screen()
        assert "Proceed with installation? [y/n]" in window.screen()
