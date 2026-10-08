"""Curses widgets of the TUI. All lists run on one engine, run_list; the text field
has its own loop.
"""

import curses
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from arch_installer.setup.frontend import Inherited


@dataclass(frozen=True)
class MenuOption:
    value: str
    label: str


@dataclass(frozen=True)
class Toggle:
    key: str
    label: str
    inherited: Inherited[bool] | None


class RowStyle(Enum):
    PLAIN = "plain"
    MARKED = "marked"  # a ticked box or a switch that is on
    HEADING = "heading"  # a group title in the summary


@dataclass(frozen=True)
class ListRow:
    text: str
    style: RowStyle


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


def _inherited_tag(inherited: Inherited | None) -> str:
    return f"  (inherited from {inherited.source})" if inherited else ""


# color pairs only exist once curses has started, so they are looked up when drawing
def _row_attributes(style: RowStyle) -> int:
    match style:
        case RowStyle.PLAIN:
            return curses.color_pair(4)
        case RowStyle.MARKED:
            return curses.color_pair(2)
        case RowStyle.HEADING:
            return curses.color_pair(1) | curses.A_BOLD


def _draw_list(
    window: curses.window,
    title: str,
    description: str,
    help_text: str,
    row_count: int,
    cursor: int,
    draw_row: Callable[[int, bool], ListRow],
    status: str,
    show_cursor: bool,
) -> int:
    window.erase()
    _draw_title(window, title)
    max_y, max_x = window.getmaxyx()
    width = max_x - BORDER_PAD * 2 - 1
    if description:
        window.addnstr(CONTENT_START - 1, BORDER_PAD, description, width, curses.color_pair(4))

    first_row = CONTENT_START + (1 if description else 0)
    status_row = max_y - HELP_ROW_OFFSET - 1
    visible_rows = max(1, status_row - 1 - first_row)
    # with a cursor the view follows it; without one the cursor is the first visible row
    scroll = max(0, cursor - visible_rows + 1) if show_cursor else cursor
    for offset, index in enumerate(range(scroll, min(row_count, scroll + visible_rows))):
        has_cursor = show_cursor and index == cursor
        row = draw_row(index, has_cursor)
        attributes = (
            curses.color_pair(6) | curses.A_BOLD if has_cursor else _row_attributes(row.style)
        )
        window.addnstr(first_row + offset, BORDER_PAD, row.text.ljust(width), width, attributes)

    indicator_column = max_x - BORDER_PAD - 3
    if scroll > 0:
        window.addstr(first_row - 1, indicator_column, "▲", curses.color_pair(3))
    if scroll + visible_rows < row_count:
        window.addstr(first_row + visible_rows, indicator_column, "▼", curses.color_pair(3))
    if status:
        window.addnstr(status_row, BORDER_PAD, status, width, curses.color_pair(5) | curses.A_BOLD)
    _draw_help(window, help_text)
    window.refresh()
    return visible_rows


# the one list engine: arrows move the cursor (or scroll, without a cursor), every other
# key goes to handle_key first, and q quits unless the menu used it. returns the cursor
# position at the key handle_key accepted
def run_list(
    window: curses.window,
    title: str,
    help_text: str,
    row_count: int,
    draw_row: Callable[[int, bool], ListRow],
    handle_key: Callable[[int, int], bool],
    start_row: int = 0,
    description: str = "",
    status: Callable[[], str] = lambda: "",
    show_cursor: bool = True,
) -> int:
    cursor = start_row
    while True:
        visible_rows = _draw_list(
            window,
            title,
            description,
            help_text,
            row_count,
            cursor,
            draw_row,
            status(),
            show_cursor,
        )
        last_position = row_count - 1 if show_cursor else max(0, row_count - visible_rows)
        key = window.getch()
        if key == curses.KEY_UP:
            cursor = max(cursor - 1, 0)
        elif key == curses.KEY_DOWN:
            cursor = min(cursor + 1, last_position)
        elif handle_key(key, cursor):
            return cursor
        elif key == KEY_Q:
            raise KeyboardInterrupt("user quit")


def radio_menu(
    window: curses.window,
    title: str,
    options: list[MenuOption],
    inherited: Inherited[str] | None,
    description: str = "",
) -> str:
    inherited_value = inherited.value if inherited else None
    start_row = next(
        (index for index, option in enumerate(options) if option.value == inherited_value), 0
    )

    def draw_row(index: int, has_cursor: bool) -> ListRow:
        option = options[index]
        marker = " ▸ " if has_cursor else "   "
        tag = _inherited_tag(inherited) if option.value == inherited_value else ""
        return ListRow(f"{marker}{option.label}{tag}", RowStyle.PLAIN)

    chosen = run_list(
        window,
        title,
        "[↑↓] Navigate  [Enter] Select  [q] Quit",
        len(options),
        draw_row,
        handle_key=lambda key, cursor: key in ENTER_KEYS,
        start_row=start_row,
        description=description,
    )
    return options[chosen].value


def checkbox_menu(
    window: curses.window,
    title: str,
    options: list[MenuOption],
    inherited: Inherited[list[str]] | None,
    description: str = "",
) -> list[str]:
    inherited_values = set(inherited.value) if inherited else set()
    checked = set(inherited_values)

    def draw_row(index: int, has_cursor: bool) -> ListRow:
        option = options[index]
        box = "[✓]" if option.value in checked else "[ ]"
        tag = _inherited_tag(inherited) if option.value in inherited_values else ""
        style = RowStyle.MARKED if option.value in checked else RowStyle.PLAIN
        return ListRow(f" {box} {option.label}{tag}", style)

    def handle_key(key: int, cursor: int) -> bool:
        if key == KEY_SPACE:
            checked.symmetric_difference_update({options[cursor].value})
            return False
        return key in ENTER_KEYS

    run_list(
        window,
        title,
        "[↑↓] Navigate  [Space] Toggle  [Enter] Confirm  [q] Quit",
        len(options),
        draw_row,
        handle_key,
        description=description,
    )
    return [option.value for option in options if option.value in checked]


@dataclass
class TextEntry:
    inherited: Inherited[str] | None
    required: bool
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
        value = self.typed or (self.inherited.value if self.inherited else "")
        if not value and self.required:
            self.error = "This field is required."
            return None
        return value


def _inherited_hint(inherited: Inherited[str], typed: str, masked: bool) -> str:
    shown = "(hidden)" if masked else inherited.value
    origin = f"Inherited from {inherited.source}: {shown}"
    if typed:
        return f"{origin}  (overridden, Esc restores it)"
    return f"{origin}  (Enter keeps it, typing replaces it)"


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
        hint = _inherited_hint(entry.inherited, entry.typed, masked)
        window.attron(curses.color_pair(3))
        window.addnstr(field_row + 3, BORDER_PAD, hint, line_width)
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
    inherited: Inherited[str] | None,
    required: bool,
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
        password = text_input(window, title, prompt, inherited=None, required=True, masked=True)
        confirm = text_input(
            window, title, "Confirm password:", inherited=None, required=True, masked=True
        )

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


SUMMARY_VALUE_WIDTH = 32


# a long value (a disk with its model) is cut so the source column stays aligned
def _fitted(value: str) -> str:
    if len(value) <= SUMMARY_VALUE_WIDTH:
        return value.ljust(SUMMARY_VALUE_WIDTH)
    return value[: SUMMARY_VALUE_WIDTH - 1] + "…"


# items are (label, value, source); a "Group.Field" label is listed under its group
def _summary_rows(items: list[tuple[str, str, str]]) -> list[ListRow]:
    rows = []
    current_group = ""
    for label, value, source in items:
        group, separator, field = label.partition(".")
        if not separator:
            rows.append(ListRow(f"  {label:<27} {_fitted(value)} {source}", RowStyle.PLAIN))
            continue
        if group != current_group:
            current_group = group
            rows.append(ListRow(f"  [{group}]", RowStyle.HEADING))
        rows.append(ListRow(f"    {field:<25} {_fitted(value)} {source}", RowStyle.PLAIN))
    return rows


def confirm_screen(
    window: curses.window,
    title: str,
    items: list[tuple[str, str, str]],
) -> bool:
    rows = _summary_rows(items)
    confirmed = False

    def handle_key(key: int, cursor: int) -> bool:
        nonlocal confirmed
        confirmed = key in (ord("y"), ord("Y"))
        return confirmed or key in (ord("n"), ord("N"), KEY_Q)

    run_list(
        window,
        title,
        "[↑↓] Scroll  [y] Confirm  [n] Cancel",
        len(rows),
        draw_row=lambda index, has_cursor: rows[index],
        handle_key=handle_key,
        status=lambda: "Proceed with installation? [y/n]",
        show_cursor=False,
    )
    return confirmed


def _toggle_line(toggle: Toggle, state: bool | None) -> str:
    switch = {True: " ON ", False: " OFF", None: "  ? "}[state]
    line = f"  [{switch}]  {toggle.label}"
    if toggle.inherited is None:
        return line + ("  (not set, press Space)" if state is None else "")
    inherited_switch = "ON" if toggle.inherited.value else "OFF"
    if state == toggle.inherited.value:
        return line + _inherited_tag(toggle.inherited)
    return line + f"  (inherited from {toggle.inherited.source}: {inherited_switch})"


# returns each toggle's chosen state; a toggle without an inherited state must be set
def toggle_menu(
    window: curses.window,
    title: str,
    toggles: list[Toggle],
    description: str = "",
) -> dict[str, bool]:
    states: dict[str, bool | None] = {
        toggle.key: toggle.inherited.value if toggle.inherited else None for toggle in toggles
    }
    message = ""

    def draw_row(index: int, has_cursor: bool) -> ListRow:
        toggle = toggles[index]
        style = RowStyle.MARKED if states[toggle.key] is True else RowStyle.PLAIN
        return ListRow(_toggle_line(toggle, states[toggle.key]), style)

    def handle_key(key: int, cursor: int) -> bool:
        nonlocal message
        if key == KEY_SPACE or key in ENTER_KEYS:
            toggle_key = toggles[cursor].key
            states[toggle_key] = states[toggle_key] is not True
            message = ""
            return False
        if key == KEY_TAB:
            unset = [toggle.label for toggle in toggles if states[toggle.key] is None]
            message = f"Set every feature first: {', '.join(unset)}" if unset else ""
            return not unset
        return False

    run_list(
        window,
        title,
        "[↑↓] Navigate  [Space/Enter] Toggle  [Tab] Continue  [q] Quit",
        len(toggles),
        draw_row,
        handle_key,
        description=description,
        status=lambda: message,
    )
    return {toggle.key: states[toggle.key] is True for toggle in toggles}


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
