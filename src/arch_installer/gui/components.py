"""reusable GUI components with cyberpunk styling.

all components are pure Tkinter with no external dependencies.
designed to work without window manager or desktop environment.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from arch_installer.gui.theme import THEME


class StyledFrame(tk.Frame):
    """dark-styled frame container."""

    def __init__(
        self,
        parent: tk.Widget,
        padding: int = THEME.padding_medium,
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            bg=THEME.bg_panel,
            highlightthickness=0,
            **kwargs,
        )
        self._padding = padding

    def pack(self, **kwargs) -> None:
        if "padx" not in kwargs:
            kwargs["padx"] = self._padding
        if "pady" not in kwargs:
            kwargs["pady"] = self._padding
        super().pack(**kwargs)


class TitleLabel(tk.Label):
    """large title text with neon accent color."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        color: str = THEME.neon_cyan,
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            text=text,
            font=THEME.get_title_font(),
            fg=color,
            bg=THEME.bg_panel,
            **kwargs,
        )


class HeaderLabel(tk.Label):
    """section header text."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        color: str = THEME.text_primary,
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            text=text,
            font=THEME.get_header_font(),
            fg=color,
            bg=THEME.bg_panel,
            **kwargs,
        )


class TextLabel(tk.Label):
    """standard text label."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        color: str = THEME.text_secondary,
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            text=text,
            font=THEME.get_label_font(),
            fg=color,
            bg=THEME.bg_panel,
            **kwargs,
        )


class HintLabel(tk.Label):
    """small hint/subtitle text."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            text=text,
            font=THEME.get_small_font(),
            fg=THEME.text_dim,
            bg=THEME.bg_panel,
            **kwargs,
        )


class CyberpunkButton(tk.Button):
    """styled button with hover effects.

    keyboard navigable with Tab and Enter keys.
    """

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command: Optional[Callable[[], None]] = None,
        accent_color: str = THEME.neon_cyan,
        width: int = THEME.button_width,
        **kwargs,
    ) -> None:
        if command is None:
            command = lambda: None
        self._accent = accent_color
        self._normal_bg = THEME.bg_input
        self._hover_bg = THEME.bg_hover

        super().__init__(
            parent,
            text=text,
            command=command,
            font=THEME.get_button_font(),
            fg=accent_color,
            bg=self._normal_bg,
            activeforeground=THEME.bg_dark,
            activebackground=accent_color,
            highlightthickness=1,
            highlightcolor=accent_color,
            highlightbackground=THEME.border_normal,
            bd=0,
            cursor="hand2",
            width=width,
            **kwargs,
        )

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_unfocus)

    def _on_enter(self, event: tk.Event) -> None:
        self.config(bg=self._hover_bg)

    def _on_leave(self, event: tk.Event) -> None:
        if not self.focus_get() == self:
            self.config(bg=self._normal_bg)

    def _on_focus(self, event: tk.Event) -> None:
        self.config(bg=self._hover_bg, highlightbackground=self._accent)

    def _on_unfocus(self, event: tk.Event) -> None:
        self.config(bg=self._normal_bg, highlightbackground=THEME.border_normal)


class MenuButton(CyberpunkButton):
    """vertical menu button for main menu navigation."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        description: str = "",
        command: Optional[Callable] = None,
        **kwargs,
    ) -> None:
        display_text = text
        if description:
            display_text = f"{text}\n{description}"

        super().__init__(
            parent,
            text=display_text,
            command=command,
            height=2,
            **kwargs,
        )


class StyledEntry(tk.Entry):
    """styled text entry field."""

    def __init__(
        self,
        parent: tk.Widget,
        placeholder: str = "",
        show: str = "",
        width: int = 40,
        **kwargs,
    ) -> None:
        self._placeholder = placeholder
        self._show_char = show
        self._has_placeholder = False

        super().__init__(
            parent,
            font=THEME.get_label_font(),
            fg=THEME.text_primary,
            bg=THEME.bg_input,
            insertbackground=THEME.neon_cyan,
            highlightthickness=1,
            highlightcolor=THEME.border_active,
            highlightbackground=THEME.border_normal,
            bd=0,
            width=width,
            **kwargs,
        )

        if show:
            self.config(show=show)

        if placeholder:
            self._show_placeholder()
            self.bind("<FocusIn>", self._on_focus_in)
            self.bind("<FocusOut>", self._on_focus_out)

    def _show_placeholder(self) -> None:
        if not self.get():
            self._has_placeholder = True
            if self._show_char:
                self.config(show="")
            self.insert(0, self._placeholder)
            self.config(fg=THEME.text_dim)

    def _hide_placeholder(self) -> None:
        if self._has_placeholder:
            self._has_placeholder = False
            self.delete(0, tk.END)
            self.config(fg=THEME.text_primary)
            if self._show_char:
                self.config(show=self._show_char)

    def _on_focus_in(self, event: tk.Event) -> None:
        self._hide_placeholder()

    def _on_focus_out(self, event: tk.Event) -> None:
        if not self.get():
            self._show_placeholder()

    def get_value(self) -> str:
        """get the entry value, excluding placeholder."""
        if self._has_placeholder:
            return ""
        return self.get()


class StyledCombobox(ttk.Combobox):
    """styled readonly combobox for selection menus."""

    def __init__(
        self,
        parent: tk.Widget,
        values: list[str],
        width: int = 37,
        **kwargs,
    ) -> None:
        # configure ttk style for this combobox
        style = ttk.Style()

        # configure the combobox styling
        style.configure(
            "Cyberpunk.TCombobox",
            fieldbackground=THEME.bg_input,
            background=THEME.bg_input,
            foreground=THEME.text_primary,
            arrowcolor=THEME.neon_cyan,
            borderwidth=0,
        )

        style.map(
            "Cyberpunk.TCombobox",
            fieldbackground=[("readonly", THEME.bg_input)],
            foreground=[("readonly", THEME.text_primary)],
            selectbackground=[("readonly", THEME.bg_selected)],
            selectforeground=[("readonly", THEME.neon_cyan)],
        )

        super().__init__(
            parent,
            values=values,
            state="readonly",
            style="Cyberpunk.TCombobox",
            font=THEME.get_label_font(),
            width=width,
            **kwargs,
        )

        if values:
            self.current(0)


class StatusIndicator(tk.Label):
    """small status indicator with icon and text."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str = "",
        status: str = "info",
        **kwargs,
    ) -> None:
        self._status_colors = {
            "success": THEME.success,
            "warning": THEME.warning,
            "error": THEME.error,
            "info": THEME.info,
        }

        icon = self._get_icon(status)
        color = self._status_colors.get(status, THEME.info)

        super().__init__(
            parent,
            text=f"{icon} {text}",
            font=THEME.get_small_font(),
            fg=color,
            bg=THEME.bg_panel,
            **kwargs,
        )

    def _get_icon(self, status: str) -> str:
        icons = {
            "success": "✓",
            "warning": "⚠",
            "error": "✗",
            "info": "ℹ",
        }
        return icons.get(status, "●")

    def set_status(self, text: str, status: str = "info") -> None:
        icon = self._get_icon(status)
        color = self._status_colors.get(status, THEME.info)
        self.config(text=f"{icon} {text}", fg=color)


class ProgressBar(tk.Frame):
    """simple progress bar with percentage display."""

    def __init__(
        self,
        parent: tk.Widget,
        width: int = 400,
        height: int = 20,
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=THEME.bg_input,
            highlightthickness=1,
            highlightbackground=THEME.border_normal,
            **kwargs,
        )
        self.pack_propagate(False)

        self._width = width
        self._progress = 0

        self._fill = tk.Frame(
            self,
            bg=THEME.neon_cyan,
            height=height - 2,
        )
        self._fill.place(x=1, y=1, width=0)

        self._label = tk.Label(
            self,
            text="0%",
            font=THEME.get_small_font(),
            fg=THEME.text_primary,
            bg=THEME.bg_input,
        )
        self._label.place(relx=0.5, rely=0.5, anchor="center")

    def set_progress(self, value: float) -> None:
        """set progress value (0.0 to 1.0)."""
        self._progress = max(0.0, min(1.0, value))
        fill_width = int((self._width - 2) * self._progress)
        self._fill.place_configure(width=fill_width)
        self._label.config(text=f"{int(self._progress * 100)}%")

        # change label color when over fill
        if self._progress > 0.5:
            self._label.config(bg=THEME.neon_cyan, fg=THEME.bg_dark)
        else:
            self._label.config(bg=THEME.bg_input, fg=THEME.text_primary)


class NavigationFrame(tk.Frame):
    """bottom navigation bar with back/next buttons."""

    def __init__(
        self,
        parent: tk.Widget,
        on_back: Optional[Callable] = None,
        on_next: Optional[Callable] = None,
        back_text: str = "← Back",
        next_text: str = "Next →",
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            bg=THEME.bg_dark,
            **kwargs,
        )

        self._back_btn = None
        self._next_btn = None

        if on_back:
            self._back_btn = CyberpunkButton(
                self,
                text=back_text,
                command=on_back,
                accent_color=THEME.text_secondary,
                width=15,
            )
            self._back_btn.pack(side=tk.LEFT, padx=THEME.padding_medium)

        if on_next:
            self._next_btn = CyberpunkButton(
                self,
                text=next_text,
                command=on_next,
                accent_color=THEME.neon_green,
                width=15,
            )
            self._next_btn.pack(side=tk.RIGHT, padx=THEME.padding_medium)

    def set_next_enabled(self, enabled: bool) -> None:
        if self._next_btn:
            state = tk.NORMAL if enabled else tk.DISABLED
            self._next_btn.config(state=state)

    def set_back_enabled(self, enabled: bool) -> None:
        if self._back_btn:
            state = tk.NORMAL if enabled else tk.DISABLED
            self._back_btn.config(state=state)
