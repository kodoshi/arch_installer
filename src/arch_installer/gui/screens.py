"""screen definitions for the installer GUI.

each screen is a separate class that handles its own layout
and user interaction. screens are pure presentation with no
business logic.
"""

import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Optional, Union

from arch_installer.gui.components import (
    CyberpunkButton,
    HeaderLabel,
    HintLabel,
    MenuButton,
    NavigationFrame,
    ProgressBar,
    StatusIndicator,
    StyledCombobox,
    StyledEntry,
    StyledFrame,
    TextLabel,
    TitleLabel,
)
from arch_installer.gui.theme import THEME


@dataclass
class ScreenCallbacks:
    """callbacks for screen navigation and data collection."""

    on_next: Optional[Callable[[], None]] = None
    on_back: Optional[Callable[[], None]] = None
    on_cancel: Optional[Callable[[], None]] = None
    on_select: Optional[Callable[[str], None]] = None
    on_submit: Optional[Callable[[dict], None]] = None


class BaseScreen(tk.Frame):
    """base class for all installer screens."""

    def __init__(
        self,
        parent: Union[tk.Widget, tk.Tk],
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, bg=THEME.bg_dark, **kwargs)  # type: ignore[arg-type]
        self._callbacks = callbacks or ScreenCallbacks()
        self._setup_keyboard_navigation()

    def _setup_keyboard_navigation(self) -> None:
        """enable keyboard navigation with Tab and arrow keys."""
        self.bind_all("<Tab>", lambda e: None)  # let default Tab work
        self.bind_all("<Return>", self._on_enter_key)
        self.bind_all("<Escape>", self._on_escape_key)
        self.bind_all("<Control-Return>", self._on_ctrl_enter_key)
        self.bind_all("<Control-n>", self._on_ctrl_enter_key)

    def _on_enter_key(self, event: tk.Event) -> None:
        """handle Enter key - activate focused widget."""
        widget = self.focus_get()
        if isinstance(widget, tk.Button):
            widget.invoke()

    def _on_ctrl_enter_key(self, event: tk.Event) -> None:
        """handle Ctrl+Enter - proceed to next screen."""
        if self._callbacks.on_next:
            self._callbacks.on_next()

    def _on_escape_key(self, event: tk.Event) -> None:
        """handle Escape key - go back or cancel."""
        if self._callbacks.on_back:
            self._callbacks.on_back()
        elif self._callbacks.on_cancel:
            self._callbacks.on_cancel()

    def show(self) -> None:
        """display this screen."""
        self.pack(fill=tk.BOTH, expand=True)
        self._set_initial_focus()

    def hide(self) -> None:
        """hide this screen."""
        self.pack_forget()

    def _set_initial_focus(self) -> None:
        """set focus to first interactive element."""
        for child in self.winfo_children():
            if isinstance(child, (tk.Button, tk.Entry)):
                child.focus_set()
                break


class WelcomeScreen(BaseScreen):
    """welcome/main menu screen.

    displays title, description, and main action buttons.
    """

    def __init__(
        self,
        parent: tk.Widget,
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, callbacks, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        # center container
        container = StyledFrame(self)
        container.pack(expand=True)

        # title
        TitleLabel(
            container,
            text="╔══════════════════════════════════════╗",
            color=THEME.neon_purple,
        ).pack(pady=(0, 0))

        TitleLabel(
            container,
            text="║     ARCH LINUX INSTALLER            ║",
        ).pack()

        TitleLabel(
            container,
            text="╚══════════════════════════════════════╝",
            color=THEME.neon_purple,
        ).pack(pady=(0, THEME.padding_large))

        # subtitle
        HintLabel(
            container,
            text="Automated installation with BTRFS, LUKS, Secure Boot",
        ).pack(pady=(0, THEME.padding_large))

        # menu buttons
        buttons_frame = StyledFrame(container)
        buttons_frame.pack()

        MenuButton(
            buttons_frame,
            text="[1] Start Installation",
            description="Fresh install with guided setup",
            command=lambda: self._select_option("install"),
            accent_color=THEME.neon_green,
        ).pack(pady=THEME.padding_small, fill=tk.X)

        MenuButton(
            buttons_frame,
            text="[2] Migration Mode",
            description="Preserve home data from existing install",
            command=lambda: self._select_option("migrate"),
            accent_color=THEME.neon_cyan,
        ).pack(pady=THEME.padding_small, fill=tk.X)

        MenuButton(
            buttons_frame,
            text="[3] Advanced Options",
            description="Custom configuration and recovery",
            command=lambda: self._select_option("advanced"),
            accent_color=THEME.neon_purple,
        ).pack(pady=THEME.padding_small, fill=tk.X)

        MenuButton(
            buttons_frame,
            text="[Q] Exit",
            description="Return to terminal",
            command=lambda: self._select_option("exit"),
            accent_color=THEME.text_dim,
        ).pack(pady=THEME.padding_small, fill=tk.X)

        # keybind hints
        HintLabel(
            container,
            text="Use Tab/Arrow keys to navigate, Enter to select, Esc to go back",
        ).pack(pady=THEME.padding_large)

        # keyboard shortcuts
        self.bind_all("1", lambda e: self._select_option("install"))
        self.bind_all("2", lambda e: self._select_option("migrate"))
        self.bind_all("3", lambda e: self._select_option("advanced"))
        self.bind_all("q", lambda e: self._select_option("exit"))
        self.bind_all("Q", lambda e: self._select_option("exit"))

    def _select_option(self, option: str) -> None:
        if self._callbacks.on_select:
            self._callbacks.on_select(option)


class DiskSelectionScreen(BaseScreen):
    """disk selection screen.

    displays available disks and allows selection.
    """

    def __init__(
        self,
        parent: tk.Widget,
        disks: list[dict],
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, callbacks, **kwargs)
        self._disks = disks
        self._selected_disk: Optional[str] = None
        self._build_ui()

    def _build_ui(self) -> None:
        # header
        header = StyledFrame(self)
        header.pack(fill=tk.X, pady=THEME.padding_medium)

        TitleLabel(header, text="SELECT TARGET DISK").pack()
        HintLabel(
            header,
            text="WARNING: Selected disk will be completely wiped!",
        ).pack()

        # disk list
        list_frame = StyledFrame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=THEME.padding_large)

        for i, disk in enumerate(self._disks):
            disk_btn = self._create_disk_button(list_frame, disk, i)
            disk_btn.pack(pady=THEME.padding_tiny, fill=tk.X)

        # navigation
        nav = NavigationFrame(
            self,
            on_back=self._callbacks.on_back,
            on_next=self._on_confirm,
            next_text="Confirm Selection →",
        )
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=THEME.padding_medium)

    def _create_disk_button(self, parent: tk.Widget, disk: dict, index: int) -> CyberpunkButton:
        path = disk.get("path", f"/dev/disk{index}")
        model = disk.get("model", "Unknown")
        size = disk.get("size", "Unknown")

        text = f"[{index}] {path}  |  {model}  |  {size}"

        btn = CyberpunkButton(
            parent,
            text=text,
            command=lambda p=path: self._select_disk(p),
            accent_color=THEME.neon_cyan if self._selected_disk != path else THEME.neon_green,
            width=60,
        )

        # bind number key
        self.bind_all(str(index), lambda e, p=path: self._select_disk(p))

        return btn

    def _select_disk(self, path: str) -> None:
        self._selected_disk = path
        if self._callbacks.on_select:
            self._callbacks.on_select(path)

    def _on_ctrl_enter_key(self, event: tk.Event) -> None:
        """override to call _on_confirm for disk selection."""
        self._on_confirm()

    def _on_confirm(self) -> None:
        if self._selected_disk and self._callbacks.on_next:
            self._callbacks.on_next()

    def get_selected_disk(self) -> Optional[str]:
        return self._selected_disk


class PasswordScreen(BaseScreen):
    """password entry screen.

    prompts for LUKS and user passwords with confirmation.
    """

    def __init__(
        self,
        parent: tk.Widget,
        password_type: str = "LUKS",
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, callbacks, **kwargs)
        self._password_type = password_type
        self._password_entry: Optional[StyledEntry] = None
        self._confirm_entry: Optional[StyledEntry] = None
        self._status: Optional[StatusIndicator] = None
        self._build_ui()

    def _build_ui(self) -> None:
        container = StyledFrame(self)
        container.pack(expand=True)

        # header
        TitleLabel(
            container,
            text=f"{self._password_type} PASSWORD",
        ).pack(pady=THEME.padding_medium)

        if self._password_type == "LUKS":
            HintLabel(
                container,
                text="This password encrypts your disk. Keep it safe!",
            ).pack(pady=THEME.padding_small)
        else:
            HintLabel(
                container,
                text="This will be your user account password.",
            ).pack(pady=THEME.padding_small)

        # password entry
        entry_frame = StyledFrame(container)
        entry_frame.pack(pady=THEME.padding_medium)

        TextLabel(entry_frame, text="Password:").pack(anchor=tk.W)
        self._password_entry = StyledEntry(
            entry_frame,
            show="●",
            placeholder="Enter password",
        )
        self._password_entry.pack(pady=THEME.padding_tiny)
        self._password_entry.bind("<KeyRelease>", self._validate_passwords)

        TextLabel(entry_frame, text="Confirm:").pack(anchor=tk.W, pady=(THEME.padding_small, 0))
        self._confirm_entry = StyledEntry(
            entry_frame,
            show="●",
            placeholder="Confirm password",
        )
        self._confirm_entry.pack(pady=THEME.padding_tiny)
        self._confirm_entry.bind("<KeyRelease>", self._validate_passwords)

        # status indicator
        self._status = StatusIndicator(container, text="", status="info")
        self._status.pack(pady=THEME.padding_small)

        # navigation
        nav = NavigationFrame(
            self,
            on_back=self._callbacks.on_back,
            on_next=self._on_submit,
            next_text="Continue →",
        )
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=THEME.padding_medium)

    def _validate_passwords(self, event: Optional[tk.Event] = None) -> bool:
        pw1 = self._password_entry.get_value() if self._password_entry else ""
        pw2 = self._confirm_entry.get_value() if self._confirm_entry else ""

        if not self._status:
            return False

        if not pw1:
            self._status.set_status("Enter a password", "info")
            return False

        if len(pw1) < 8:
            # show warning but don't block - user can still proceed
            if not pw2:
                self._status.set_status(
                    "Warning: Password is short. Confirm to continue", "warning"
                )
                return False
            if pw1 != pw2:
                self._status.set_status("Passwords do not match", "error")
                return False
            self._status.set_status("Warning: Short password accepted", "warning")
            return True

        if not pw2:
            self._status.set_status("Confirm your password", "info")
            return False

        if pw1 != pw2:
            self._status.set_status("Passwords do not match", "error")
            return False

        self._status.set_status("Passwords match", "success")
        return True

    def _on_ctrl_enter_key(self, event: tk.Event) -> None:
        """override to call _on_submit for password screens."""
        self._on_submit()

    def _on_submit(self) -> None:
        if self._validate_passwords() and self._callbacks.on_submit:
            self._callbacks.on_submit(
                {
                    "password": self._password_entry.get_value() if self._password_entry else "",
                }
            )

    def get_password(self) -> str:
        return self._password_entry.get_value() if self._password_entry else ""


class SelectionScreen(BaseScreen):
    """generic selection screen with combobox or buttons.

    used for GPU, CPU, desktop environment, etc.
    """

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        options: list[dict],
        hint: str = "",
        allow_multiple: bool = False,
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, callbacks, **kwargs)
        self._title = title
        self._options = options
        self._hint = hint
        self._allow_multiple = allow_multiple
        self._selected: list[str] = []
        self._buttons: dict[str, CyberpunkButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        container = StyledFrame(self)
        container.pack(expand=True)

        # header
        TitleLabel(container, text=self._title).pack(pady=THEME.padding_medium)

        if self._hint:
            HintLabel(container, text=self._hint).pack(pady=THEME.padding_small)

        # options
        options_frame = StyledFrame(container)
        options_frame.pack(pady=THEME.padding_medium)

        for i, opt in enumerate(self._options):
            value = opt.get("value", str(i))
            label = opt.get("label", value)
            desc = opt.get("description", "")

            display = f"[{i + 1}] {label}"
            if desc:
                display += f"\n    {desc}"

            btn = CyberpunkButton(
                options_frame,
                text=display,
                command=lambda v=value: self._toggle_option(v),
                width=50,
            )
            btn.pack(pady=THEME.padding_tiny)
            self._buttons[value] = btn

            # bind number key
            self.bind_all(str(i + 1), lambda e, v=value: self._toggle_option(v))

        # navigation
        nav = NavigationFrame(
            self,
            on_back=self._callbacks.on_back,
            on_next=self._on_confirm,
            next_text="Continue →",
        )
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=THEME.padding_medium)

    def _toggle_option(self, value: str) -> None:
        if self._allow_multiple:
            if value in self._selected:
                self._selected.remove(value)
            else:
                self._selected.append(value)
        else:
            self._selected = [value]

        # update button styling
        for v, btn in self._buttons.items():
            if v in self._selected:
                btn.config(fg=THEME.neon_green)
            else:
                btn.config(fg=THEME.neon_cyan)

        if self._callbacks.on_select:
            self._callbacks.on_select(value)

    def _on_ctrl_enter_key(self, event: tk.Event) -> None:
        """override to call _on_confirm for selection screens."""
        self._on_confirm()

    def _on_confirm(self) -> None:
        if self._callbacks.on_next:
            self._callbacks.on_next()

    def get_selected(self) -> list[str]:
        return self._selected.copy()


class ConfirmationScreen(BaseScreen):
    """confirmation screen before installation begins.

    shows summary of all selections.
    """

    def __init__(
        self,
        parent: tk.Widget,
        config_summary: dict,
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, callbacks, **kwargs)
        self._summary = config_summary
        self._build_ui()

    def _build_ui(self) -> None:
        container = StyledFrame(self)
        container.pack(expand=True)

        # header
        TitleLabel(
            container,
            text="CONFIRM INSTALLATION",
            color=THEME.neon_orange,
        ).pack(pady=THEME.padding_medium)

        HintLabel(
            container,
            text="Review your selections. Installation will begin after confirmation.",
        ).pack(pady=THEME.padding_small)

        # summary table
        summary_frame = StyledFrame(container)
        summary_frame.pack(pady=THEME.padding_medium, fill=tk.X)

        for key, value in self._summary.items():
            row = tk.Frame(summary_frame, bg=THEME.bg_panel)
            row.pack(fill=tk.X, pady=2)

            TextLabel(
                row,
                text=f"{key}:",
                color=THEME.text_secondary,
            ).pack(side=tk.LEFT, padx=THEME.padding_small)

            TextLabel(
                row,
                text=str(value),
                color=THEME.neon_cyan,
            ).pack(side=tk.LEFT)

        # warning
        warning_frame = StyledFrame(container)
        warning_frame.pack(pady=THEME.padding_large)

        StatusIndicator(
            warning_frame,
            text="ALL DATA ON THE TARGET DISK WILL BE DESTROYED",
            status="warning",
        ).pack()

        # navigation with prominent install button
        nav_frame = tk.Frame(self, bg=THEME.bg_dark)
        nav_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=THEME.padding_medium)

        CyberpunkButton(
            nav_frame,
            text="← Back",
            command=self._callbacks.on_back,
            accent_color=THEME.text_secondary,
            width=15,
        ).pack(side=tk.LEFT, padx=THEME.padding_medium)

        CyberpunkButton(
            nav_frame,
            text="⚡ BEGIN INSTALLATION ⚡",
            command=self._callbacks.on_next,
            accent_color=THEME.neon_green,
            width=25,
        ).pack(side=tk.RIGHT, padx=THEME.padding_medium)


class ProgressScreen(BaseScreen):
    """installation progress screen.

    shows current step, progress bar, and log output.
    """

    def __init__(
        self,
        parent: tk.Widget,
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, callbacks, **kwargs)
        self._current_step: Optional[TextLabel] = None
        self._progress: Optional[ProgressBar] = None
        self._log_text: Optional[tk.Text] = None
        self._build_ui()

    def _build_ui(self) -> None:
        container = StyledFrame(self)
        container.pack(fill=tk.BOTH, expand=True)

        # header
        TitleLabel(
            container,
            text="INSTALLING...",
            color=THEME.neon_cyan,
        ).pack(pady=THEME.padding_medium)

        # current step
        self._current_step = TextLabel(
            container,
            text="Preparing installation...",
            color=THEME.text_primary,
        )
        self._current_step.pack(pady=THEME.padding_small)

        # progress bar
        self._progress = ProgressBar(container, width=600)
        self._progress.pack(pady=THEME.padding_medium)

        # log output
        log_frame = StyledFrame(container)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=THEME.padding_large)

        HeaderLabel(log_frame, text="Installation Log").pack(anchor=tk.W)

        self._log_text = tk.Text(
            log_frame,
            font=THEME.get_small_font(),
            fg=THEME.text_secondary,
            bg=THEME.bg_input,
            insertbackground=THEME.neon_cyan,
            highlightthickness=1,
            highlightbackground=THEME.border_normal,
            bd=0,
            height=15,
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self._log_text.pack(fill=tk.BOTH, expand=True, pady=THEME.padding_small)

    def set_step(self, step_name: str) -> None:
        """update the current step display."""
        if self._current_step:
            self._current_step.config(text=step_name)

    def set_progress(self, value: float) -> None:
        """update progress bar (0.0 to 1.0)."""
        if self._progress:
            self._progress.set_progress(value)

    def append_log(self, text: str) -> None:
        """append text to the log output."""
        if self._log_text:
            self._log_text.config(state=tk.NORMAL)
            self._log_text.insert(tk.END, text + "\n")
            self._log_text.see(tk.END)
            self._log_text.config(state=tk.DISABLED)


class CompletionScreen(BaseScreen):
    """installation complete screen.

    shows success message and next steps.
    """

    def __init__(
        self,
        parent: tk.Widget,
        success: bool = True,
        message: str = "",
        callbacks: Optional[ScreenCallbacks] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, callbacks, **kwargs)
        self._success = success
        self._message = message
        self._build_ui()

    def _build_ui(self) -> None:
        container = StyledFrame(self)
        container.pack(expand=True)

        if self._success:
            # success display
            TitleLabel(
                container,
                text="╔═══════════════════════════════════════╗",
                color=THEME.neon_green,
            ).pack()
            TitleLabel(
                container,
                text="║     INSTALLATION COMPLETE!            ║",
                color=THEME.neon_green,
            ).pack()
            TitleLabel(
                container,
                text="╚═══════════════════════════════════════╝",
                color=THEME.neon_green,
            ).pack(pady=(0, THEME.padding_large))

            TextLabel(
                container,
                text="Your Arch Linux system is ready.",
                color=THEME.text_primary,
            ).pack(pady=THEME.padding_small)

            HintLabel(
                container,
                text="Remove the installation media and reboot to start using your system.",
            ).pack(pady=THEME.padding_small)

        else:
            # error display
            TitleLabel(
                container,
                text="╔═══════════════════════════════════════╗",
                color=THEME.error,
            ).pack()
            TitleLabel(
                container,
                text="║     INSTALLATION FAILED               ║",
                color=THEME.error,
            ).pack()
            TitleLabel(
                container,
                text="╚═══════════════════════════════════════╝",
                color=THEME.error,
            ).pack(pady=(0, THEME.padding_large))

            if self._message:
                TextLabel(
                    container,
                    text=self._message,
                    color=THEME.text_primary,
                ).pack(pady=THEME.padding_small)

        # buttons
        button_frame = StyledFrame(container)
        button_frame.pack(pady=THEME.padding_large)

        CyberpunkButton(
            button_frame,
            text="Reboot Now",
            command=lambda: (
                self._callbacks.on_select("reboot") if self._callbacks.on_select else None
            ),
            accent_color=THEME.neon_green if self._success else THEME.neon_cyan,
        ).pack(pady=THEME.padding_small)

        CyberpunkButton(
            button_frame,
            text="Exit to Terminal",
            command=lambda: (
                self._callbacks.on_select("exit") if self._callbacks.on_select else None
            ),
            accent_color=THEME.text_secondary,
        ).pack(pady=THEME.padding_small)
