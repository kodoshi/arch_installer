"""curses widget primitives for the TUI installer.

each widget handles its own rendering and input loop, returning
the user's selection when complete. all widgets are stateless
functions or simple classes that don't depend on global state.

every widget starts on the value inherited from the resolved config and marks it,
so Enter keeps it and any other choice overrides it.
"""

import curses
from dataclasses import dataclass


@dataclass(frozen=True)
class MenuOption:
    value: str
    label: str


# key constants
KEY_ESCAPE = 27
KEY_TAB = 9
KEY_SPACE = ord(" ")
KEY_Q = ord("q")
ENTER_KEYS = (curses.KEY_ENTER, 10, 13)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)
PRINTABLE_KEYS = range(32, 127)

# layout
BORDER_PAD = 2
TITLE_ROW = 1
CONTENT_START = 4
HELP_ROW_OFFSET = 2

INHERITED_TAG = "(inherited)"


def init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)  # title / highlighted
    curses.init_pair(2, curses.COLOR_GREEN, -1)  # selected item
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # help text
    curses.init_pair(4, curses.COLOR_WHITE, -1)  # normal text
    curses.init_pair(5, curses.COLOR_RED, -1)  # error / warning
    curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)  # cursor highlight


def _draw_title(window: curses.window, title: str) -> None:
    _max_y, max_x = window.getmaxyx()
    window.attron(curses.color_pair(1) | curses.A_BOLD)
    centered = title.center(max_x - BORDER_PAD * 2)
    window.addstr(TITLE_ROW, BORDER_PAD, centered[: max_x - BORDER_PAD * 2 - 1])
    window.attroff(curses.color_pair(1) | curses.A_BOLD)
    window.addstr(TITLE_ROW + 1, BORDER_PAD, "─" * (max_x - BORDER_PAD * 2 - 1))


def _draw_help(window: curses.window, help_text: str) -> None:
    max_y, max_x = window.getmaxyx()
    row = max_y - HELP_ROW_OFFSET
    window.attron(curses.color_pair(3))
    window.addstr(row, BORDER_PAD, help_text[: max_x - BORDER_PAD * 2 - 1])
    window.attroff(curses.color_pair(3))


def _tagged(label: str, is_inherited: bool) -> str:
    return f"{label}  {INHERITED_TAG}" if is_inherited else label


def radio_menu(
    window: curses.window,
    title: str,
    options: list[MenuOption],
    inherited_value: str = "",
    description: str = "",
) -> str:
    cursor = next(
        (index for index, option in enumerate(options) if option.value == inherited_value), 0
    )

    while True:
        window.erase()
        _draw_title(window, title)
        max_y, max_x = window.getmaxyx()

        if description:
            window.attron(curses.color_pair(4))
            window.addstr(CONTENT_START - 1, BORDER_PAD, description[: max_x - BORDER_PAD * 2 - 1])
            window.attroff(curses.color_pair(4))

        visible_start = CONTENT_START + (1 if description else 0)
        max_visible = max_y - visible_start - HELP_ROW_OFFSET - 1

        # scroll if needed
        scroll_offset = 0
        if cursor >= max_visible:
            scroll_offset = cursor - max_visible + 1

        for visible_row in range(min(len(options), max_visible)):
            option_index = visible_row + scroll_offset
            if option_index >= len(options):
                break

            option = options[option_index]
            row = visible_start + visible_row
            is_cursor = option_index == cursor

            if is_cursor:
                window.attron(curses.color_pair(6) | curses.A_BOLD)
                marker = " ▸ "
            else:
                window.attron(curses.color_pair(4))
                marker = "   "

            line = f"{marker}{_tagged(option.label, option.value == inherited_value)}"
            window.addnstr(row, BORDER_PAD, line, max_x - BORDER_PAD * 2 - 1)

            if is_cursor:
                # fill rest of line with highlight
                remaining = max_x - BORDER_PAD * 2 - len(line) - 1
                if remaining > 0:
                    window.addstr(" " * remaining)
                window.attroff(curses.color_pair(6) | curses.A_BOLD)
            else:
                window.attroff(curses.color_pair(4))

        # scroll indicators
        if scroll_offset > 0:
            window.addstr(visible_start - 1, max_x - BORDER_PAD - 3, "▲", curses.color_pair(3))
        if scroll_offset + max_visible < len(options):
            window.addstr(
                visible_start + max_visible, max_x - BORDER_PAD - 3, "▼", curses.color_pair(3)
            )

        _draw_help(window, "[↑↓] Navigate  [Enter] Select  [q] Quit")
        window.refresh()

        key = window.getch()
        if key == curses.KEY_UP and cursor > 0:
            cursor -= 1
        elif key == curses.KEY_DOWN and cursor < len(options) - 1:
            cursor += 1
        elif key in ENTER_KEYS:
            return options[cursor].value
        elif key == KEY_Q:
            raise KeyboardInterrupt("user quit")


def checkbox_menu(
    window: curses.window,
    title: str,
    options: list[MenuOption],
    inherited_values: list[str] | None = None,
    description: str = "",
) -> list[str]:
    cursor = 0
    inherited = set(inherited_values or [])
    checked = set(inherited)

    while True:
        window.erase()
        _draw_title(window, title)
        max_y, max_x = window.getmaxyx()

        if description:
            window.attron(curses.color_pair(4))
            window.addstr(CONTENT_START - 1, BORDER_PAD, description[: max_x - BORDER_PAD * 2 - 1])
            window.attroff(curses.color_pair(4))

        visible_start = CONTENT_START + (1 if description else 0)
        max_visible = max_y - visible_start - HELP_ROW_OFFSET - 1

        scroll_offset = 0
        if cursor >= max_visible:
            scroll_offset = cursor - max_visible + 1

        for visible_row in range(min(len(options), max_visible)):
            option_index = visible_row + scroll_offset
            if option_index >= len(options):
                break

            option = options[option_index]
            row = visible_start + visible_row
            is_cursor = option_index == cursor
            is_checked = option.value in checked

            checkbox = "[✓]" if is_checked else "[ ]"

            if is_cursor:
                window.attron(curses.color_pair(6) | curses.A_BOLD)
            elif is_checked:
                window.attron(curses.color_pair(2))
            else:
                window.attron(curses.color_pair(4))

            line = f" {checkbox} {_tagged(option.label, option.value in inherited)}"
            window.addnstr(row, BORDER_PAD, line, max_x - BORDER_PAD * 2 - 1)

            if is_cursor:
                remaining = max_x - BORDER_PAD * 2 - len(line) - 1
                if remaining > 0:
                    window.addstr(" " * remaining)
                window.attroff(curses.color_pair(6) | curses.A_BOLD)
            elif is_checked:
                window.attroff(curses.color_pair(2))
            else:
                window.attroff(curses.color_pair(4))

        _draw_help(window, "[↑↓] Navigate  [Space] Toggle  [Enter] Confirm  [q] Quit")
        window.refresh()

        key = window.getch()
        if key == curses.KEY_UP and cursor > 0:
            cursor -= 1
        elif key == curses.KEY_DOWN and cursor < len(options) - 1:
            cursor += 1
        elif key == KEY_SPACE:
            toggled_value = options[cursor].value
            if toggled_value in checked:
                checked.discard(toggled_value)
            else:
                checked.add(toggled_value)
        elif key in ENTER_KEYS:
            return [option.value for option in options if option.value in checked]
        elif key == KEY_Q:
            raise KeyboardInterrupt("user quit")


@dataclass
class TextEntry:
    inherited: str = ""
    required: bool = False
    typed: str = ""
    error: str = ""

    # returns the submitted value once Enter is accepted, None while still editing.
    # every printable key is text, so quitting a text field is Ctrl+C only
    def press(self, key: int) -> str | None:
        if key in ENTER_KEYS:
            return self._submit()
        if key == KEY_ESCAPE:
            self.typed = ""
        elif key in BACKSPACE_KEYS:
            self.typed = self.typed[:-1]
        elif key in PRINTABLE_KEYS:
            self.typed += chr(key)
        else:
            return None
        self.error = ""
        return None

    def _submit(self) -> str | None:
        value = self.typed or self.inherited
        if not value and self.required:
            self.error = "This field is required."
            return None
        return value


def _inherited_hint(entry: TextEntry, masked: bool) -> str:
    shown = "(hidden)" if masked else entry.inherited
    if entry.typed:
        return f"Inherited: {shown}  (overridden, Esc restores it)"
    return f"Inherited: {shown}  (Enter keeps it, typing replaces it)"


def _draw_text_entry(
    window: curses.window, title: str, prompt: str, entry: TextEntry, masked: bool
) -> None:
    window.erase()
    _draw_title(window, title)
    _max_y, max_x = window.getmaxyx()
    line_width = max_x - BORDER_PAD * 2 - 1

    window.attron(curses.color_pair(4))
    window.addstr(CONTENT_START, BORDER_PAD, prompt)
    window.attroff(curses.color_pair(4))

    # draw input field
    field_row = CONTENT_START + 2
    field_width = max_x - BORDER_PAD * 2 - 4
    display_text = "*" * len(entry.typed) if masked else entry.typed

    # input box border
    window.addstr(field_row - 1, BORDER_PAD, "┌" + "─" * (field_width + 2) + "┐")
    window.addstr(field_row, BORDER_PAD, "│ ")
    window.attron(curses.color_pair(1))
    visible_text = display_text[-field_width:]
    window.addstr(visible_text.ljust(field_width))
    window.attroff(curses.color_pair(1))
    window.addstr(" │")
    window.addstr(field_row + 1, BORDER_PAD, "└" + "─" * (field_width + 2) + "┘")

    if entry.inherited:
        window.attron(curses.color_pair(3))
        window.addnstr(field_row + 3, BORDER_PAD, _inherited_hint(entry, masked), line_width)
        window.attroff(curses.color_pair(3))

    if entry.error:
        window.attron(curses.color_pair(5))
        window.addnstr(field_row + 4, BORDER_PAD, entry.error, line_width)
        window.attroff(curses.color_pair(5))

    _draw_help(window, "[Enter] Confirm  [Esc] Clear  [Ctrl+C] Quit")
    window.refresh()


def text_input(
    window: curses.window,
    title: str,
    prompt: str,
    inherited: str = "",
    required: bool = False,
    masked: bool = False,
) -> str:
    entry = TextEntry(inherited=inherited, required=required)
    while True:
        _draw_text_entry(window, title, prompt, entry, masked)
        submitted = entry.press(window.getch())
        if submitted is not None:
            return submitted


def password_input_with_confirm(
    window: curses.window,
    title: str,
    prompt: str,
) -> str:
    while True:
        password = text_input(window, title, prompt, required=True, masked=True)
        confirm = text_input(window, title, "Confirm password:", required=True, masked=True)

        if password == confirm:
            return password

        window.erase()
        _draw_title(window, title)
        window.attron(curses.color_pair(5) | curses.A_BOLD)
        window.addstr(CONTENT_START + 2, BORDER_PAD, "Passwords do not match. Try again.")
        window.attroff(curses.color_pair(5) | curses.A_BOLD)
        _draw_help(window, "Press any key to retry...")
        window.refresh()
        window.getch()


# items are (label, value) pairs; a "Group.Field" label is listed under its group
def confirm_screen(
    window: curses.window,
    title: str,
    items: list[tuple[str, str]],
) -> bool:
    scroll = 0

    while True:
        window.erase()
        _draw_title(window, title)
        max_y, max_x = window.getmaxyx()

        visible_start = CONTENT_START
        max_visible = max_y - visible_start - HELP_ROW_OFFSET - 3

        current_group = ""
        display_lines: list[tuple[str, str, bool]] = []  # (text, color_pair, is_group_header)

        for label, value in items:
            # detect group changes by checking if there's a category prefix
            parts = label.split(".", 1)
            if len(parts) == 2:
                group, field = parts
                if group != current_group:
                    current_group = group
                    display_lines.append((f"  [{group}]", "group", True))
                display_lines.append((f"    {field:<25} {value}", "item", False))
            else:
                display_lines.append((f"  {label:<27} {value}", "item", False))

        for visible_row in range(min(len(display_lines), max_visible)):
            line_index = visible_row + scroll
            if line_index >= len(display_lines):
                break

            text, _style, is_header = display_lines[line_index]
            row = visible_start + visible_row

            if is_header:
                window.attron(curses.color_pair(1) | curses.A_BOLD)
            else:
                window.attron(curses.color_pair(4))

            window.addnstr(row, BORDER_PAD, text, max_x - BORDER_PAD * 2 - 1)

            if is_header:
                window.attroff(curses.color_pair(1) | curses.A_BOLD)
            else:
                window.attroff(curses.color_pair(4))

        # scroll indicators
        if scroll > 0:
            window.addstr(visible_start - 1, max_x - BORDER_PAD - 3, "▲", curses.color_pair(3))
        if scroll + max_visible < len(display_lines):
            row_ind = visible_start + max_visible
            if row_ind < max_y - 1:
                window.addstr(row_ind, max_x - BORDER_PAD - 3, "▼", curses.color_pair(3))

        # confirmation prompt
        confirm_row = max_y - HELP_ROW_OFFSET - 1
        window.attron(curses.color_pair(5) | curses.A_BOLD)
        window.addnstr(
            confirm_row,
            BORDER_PAD,
            "Proceed with installation? [y/n]",
            max_x - BORDER_PAD * 2 - 1,
        )
        window.attroff(curses.color_pair(5) | curses.A_BOLD)

        _draw_help(window, "[↑↓] Scroll  [y] Confirm  [n] Cancel")
        window.refresh()

        key = window.getch()
        if key == curses.KEY_UP and scroll > 0:
            scroll -= 1
        elif key == curses.KEY_DOWN and scroll + max_visible < len(display_lines):
            scroll += 1
        elif key in (ord("y"), ord("Y")):
            return True
        elif key in (ord("n"), ord("N"), KEY_Q):
            return False


def _toggle_line(label: str, is_on: bool, inherited_on: bool) -> str:
    switch = " ON " if is_on else " OFF"
    line = f"  [{switch}]  {label}"
    if is_on != inherited_on:
        line += f"  (inherited: {'ON' if inherited_on else 'OFF'})"
    return line


# toggles are (key, label, inherited state); returns (key, chosen state) pairs
def toggle_menu(
    window: curses.window,
    title: str,
    toggles: list[tuple[str, str, bool]],
    description: str = "",
) -> list[tuple[str, bool]]:
    cursor = 0
    states = {key: inherited for key, _, inherited in toggles}

    while True:
        window.erase()
        _draw_title(window, title)
        _max_y, max_x = window.getmaxyx()

        if description:
            window.attron(curses.color_pair(4))
            window.addstr(CONTENT_START - 1, BORDER_PAD, description[: max_x - BORDER_PAD * 2 - 1])
            window.attroff(curses.color_pair(4))

        visible_start = CONTENT_START + (1 if description else 0)

        for toggle_index, (key, label, inherited_on) in enumerate(toggles):
            row = visible_start + toggle_index
            is_cursor = toggle_index == cursor
            is_on = states[key]

            if is_cursor:
                window.attron(curses.color_pair(6) | curses.A_BOLD)
            else:
                window.attron(curses.color_pair(2) if is_on else curses.color_pair(4))

            line = _toggle_line(label, is_on, inherited_on)
            window.addnstr(row, BORDER_PAD, line, max_x - BORDER_PAD * 2 - 1)

            if is_cursor:
                remaining = max_x - BORDER_PAD * 2 - len(line) - 1
                if remaining > 0:
                    window.addstr(" " * remaining)
                window.attroff(curses.color_pair(6) | curses.A_BOLD)
            else:
                window.attroff(curses.color_pair(2) if is_on else curses.color_pair(4))

        _draw_help(window, "[↑↓] Navigate  [Space/Enter] Toggle  [Tab] Continue  [q] Quit")
        window.refresh()

        key_pressed = window.getch()
        if key_pressed == curses.KEY_UP and cursor > 0:
            cursor -= 1
        elif key_pressed == curses.KEY_DOWN and cursor < len(toggles) - 1:
            cursor += 1
        elif key_pressed == KEY_SPACE or key_pressed in ENTER_KEYS:
            toggle_key = toggles[cursor][0]
            states[toggle_key] = not states[toggle_key]
        elif key_pressed == KEY_TAB:
            return [(toggle_key, states[toggle_key]) for toggle_key, _, _ in toggles]
        elif key_pressed == KEY_Q:
            raise KeyboardInterrupt("user quit")


def info_screen(
    window: curses.window,
    title: str,
    message: str,
) -> None:
    window.erase()
    _draw_title(window, title)
    max_y, max_x = window.getmaxyx()

    lines = message.split("\n")
    for line_number, line in enumerate(lines):
        row = CONTENT_START + line_number
        if row >= max_y - HELP_ROW_OFFSET:
            break
        window.attron(curses.color_pair(4))
        window.addnstr(row, BORDER_PAD, line, max_x - BORDER_PAD * 2 - 1)
        window.attroff(curses.color_pair(4))

    _draw_help(window, "Press any key to continue...")
    window.refresh()
    window.getch()
