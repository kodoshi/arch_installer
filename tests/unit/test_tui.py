import pytest

from arch_installer.tui.app import swap_size_options
from arch_installer.tui.widgets import KEY_ESCAPE, TextEntry

KEY_ENTER = 10
KEY_BACKSPACE = 127


def type_text(entry: TextEntry, text: str) -> None:
    for character in text:
        assert entry.press(ord(character)) is None


class TestTextEntry:
    def test_enter_on_an_untouched_field_keeps_the_inherited_value(self):
        entry = TextEntry(inherited="testmaximal")

        submitted = entry.press(KEY_ENTER)

        assert submitted == "testmaximal"

    def test_typing_replaces_the_inherited_value(self):
        entry = TextEntry(inherited="testmaximal")

        type_text(entry, "myhost")
        submitted = entry.press(KEY_ENTER)

        assert submitted == "myhost"

    @pytest.mark.parametrize("text", ["aqua", "q", "quasar"])
    def test_the_letter_q_is_typed_like_any_other_character(self, text):
        entry = TextEntry()

        type_text(entry, text)
        submitted = entry.press(KEY_ENTER)

        assert submitted == text

    def test_escape_drops_the_override_and_restores_the_inherited_value(self):
        entry = TextEntry(inherited="us")
        type_text(entry, "fi")

        entry.press(KEY_ESCAPE)
        submitted = entry.press(KEY_ENTER)

        assert submitted == "us"

    def test_backspace_removes_the_last_typed_character(self):
        entry = TextEntry()
        type_text(entry, "vdab")

        entry.press(KEY_BACKSPACE)
        submitted = entry.press(KEY_ENTER)

        assert submitted == "vda"

    def test_required_field_without_value_refuses_enter_and_shows_an_error(self):
        entry = TextEntry(required=True)

        submitted = entry.press(KEY_ENTER)

        assert submitted is None
        assert entry.error

    def test_typing_after_a_refusal_clears_the_error(self):
        entry = TextEntry(required=True)
        entry.press(KEY_ENTER)

        type_text(entry, "x")

        assert not entry.error

    def test_optional_empty_field_submits_an_empty_value(self):
        entry = TextEntry()

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
