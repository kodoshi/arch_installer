"""main application class for the installer GUI.

coordinates screen navigation and provides the root window
configuration for headless/framebuffer operation.
"""

import tkinter as tk
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Optional

from arch_installer.gui.screens import (
    BaseScreen,
    CompletionScreen,
    ConfirmationScreen,
    DiskSelectionScreen,
    PasswordScreen,
    ProgressScreen,
    ScreenCallbacks,
    SelectionScreen,
    WelcomeScreen,
)
from arch_installer.gui.theme import THEME


class ScreenId(Enum):
    """identifiers for all application screens."""

    WELCOME = auto()
    DISK_SELECTION = auto()
    LUKS_PASSWORD = auto()
    USER_PASSWORD = auto()
    GPU_SELECTION = auto()
    CPU_SELECTION = auto()
    DESKTOP_SELECTION = auto()
    SWAP_SELECTION = auto()
    FEATURE_SELECTION = auto()
    CONFIRMATION = auto()
    PROGRESS = auto()
    COMPLETION = auto()


@dataclass
class InstallerState:
    """holds all user selections during installation flow."""

    mode: str = ""  # install, migrate, advanced
    target_disk: str = ""
    luks_password: str = ""
    user_password: str = ""
    gpu_vendor: str = "none"
    gpu_driver: str = ""
    cpu_vendor: str = "amd"
    desktop_environments: Optional[list[str]] = None
    swap_size_mb: int = 8192
    enable_hibernation: bool = True
    enable_firewall: bool = True
    enable_snapshot_boot: bool = True

    def __post_init__(self):
        if self.desktop_environments is None:
            self.desktop_environments = []

    def to_summary(self) -> dict:
        """convert to a summary dict for display."""
        desktops = self.desktop_environments or []
        return {
            "Target Disk": self.target_disk or "Not selected",
            "GPU": self.gpu_vendor,
            "CPU": self.cpu_vendor,
            "Desktop": ", ".join(desktops) if desktops else "None",
            "Swap Size": f"{self.swap_size_mb} MB",
            "Hibernation": "Yes" if self.enable_hibernation else "No",
            "Firewall": "Yes" if self.enable_firewall else "No",
            "Bootable Snapshots": "Yes" if self.enable_snapshot_boot else "No",
        }


class InstallerApp:
    """main GUI application for the arch installer.

    manages window creation, screen navigation, and state collection.
    designed to work in minimal environments without window manager.
    """

    def __init__(
        self,
        on_install: Optional[Callable[[InstallerState], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        fullscreen: bool = False,
    ) -> None:
        self._on_install = on_install
        self._on_exit = on_exit
        self._fullscreen = fullscreen

        self._root: Optional[tk.Tk] = None
        self._current_screen: Optional[BaseScreen] = None
        self._state = InstallerState()
        self._disks: list[dict] = []

    def _create_window(self) -> tk.Tk:
        """create and configure the main window."""
        root = tk.Tk()
        root.title("Arch Linux Installer")
        root.configure(bg=THEME.bg_dark)

        if self._fullscreen:
            root.attributes("-fullscreen", True)
        else:
            # fixed window size
            root.geometry(f"{THEME.window_width}x{THEME.window_height}")
            root.resizable(False, False)

        # center window on screen
        root.update_idletasks()
        x = (root.winfo_screenwidth() - THEME.window_width) // 2
        y = (root.winfo_screenheight() - THEME.window_height) // 2
        root.geometry(f"+{x}+{y}")

        # configure for minimal environment
        root.option_add("*tearOff", False)

        # keyboard shortcuts for emergency exit
        root.bind("<Control-c>", lambda e: self._exit())
        root.bind("<Control-q>", lambda e: self._exit())

        return root

    def set_disks(self, disks: list[dict]) -> None:
        """set available disks for selection.

        args:
            disks: list of dicts with keys: path, model, size
        """
        self._disks = disks

    def run(self) -> None:
        """start the GUI application main loop."""
        self._root = self._create_window()
        self._show_screen(ScreenId.WELCOME)
        self._root.mainloop()

    def _exit(self) -> None:
        """exit the application."""
        if self._on_exit:
            self._on_exit()
        if self._root:
            self._root.destroy()

    def _show_screen(self, screen_id: ScreenId) -> None:
        """display a specific screen."""
        if self._current_screen:
            self._current_screen.hide()
            self._current_screen.destroy()

        screen = self._create_screen(screen_id)
        if screen:
            self._current_screen = screen
            screen.show()

    def _create_screen(self, screen_id: ScreenId) -> Optional[BaseScreen]:
        """create a screen instance for the given ID."""
        if self._root is None:
            return None

        if screen_id == ScreenId.WELCOME:
            return WelcomeScreen(
                self._root,
                callbacks=ScreenCallbacks(
                    on_select=self._on_welcome_select,
                ),
            )

        elif screen_id == ScreenId.DISK_SELECTION:
            return DiskSelectionScreen(
                self._root,
                disks=self._disks or self._get_default_disks(),
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.WELCOME),
                    on_next=self._on_disk_confirmed,
                    on_select=self._on_disk_selected,
                ),
            )

        elif screen_id == ScreenId.LUKS_PASSWORD:
            return PasswordScreen(
                self._root,
                password_type="LUKS",
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.DISK_SELECTION),
                    on_submit=self._on_luks_password_submitted,
                ),
            )

        elif screen_id == ScreenId.USER_PASSWORD:
            return PasswordScreen(
                self._root,
                password_type="USER",
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.LUKS_PASSWORD),
                    on_submit=self._on_user_password_submitted,
                ),
            )

        elif screen_id == ScreenId.GPU_SELECTION:
            return SelectionScreen(
                self._root,
                title="GPU VENDOR",
                options=[
                    {"value": "amd", "label": "AMD", "description": "AMDGPU open-source driver"},
                    {"value": "intel", "label": "Intel", "description": "Integrated graphics"},
                    {"value": "nvidia", "label": "NVIDIA", "description": "Proprietary driver"},
                    {
                        "value": "none",
                        "label": "None",
                        "description": "Virtual machine or generic",
                    },
                ],
                hint="Select your graphics card vendor",
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.USER_PASSWORD),
                    on_next=self._on_gpu_selected,
                    on_select=lambda v: setattr(self._state, "gpu_vendor", v),
                ),
            )

        elif screen_id == ScreenId.CPU_SELECTION:
            return SelectionScreen(
                self._root,
                title="CPU VENDOR",
                options=[
                    {"value": "amd", "label": "AMD", "description": "AMD processors"},
                    {"value": "intel", "label": "Intel", "description": "Intel processors"},
                ],
                hint="Select your CPU vendor for microcode updates",
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.GPU_SELECTION),
                    on_next=self._on_cpu_selected,
                    on_select=lambda v: setattr(self._state, "cpu_vendor", v),
                ),
            )

        elif screen_id == ScreenId.DESKTOP_SELECTION:
            return SelectionScreen(
                self._root,
                title="DESKTOP ENVIRONMENT",
                options=[
                    {"value": "gnome", "label": "GNOME", "description": "Modern Wayland desktop"},
                    {
                        "value": "kde",
                        "label": "KDE Plasma",
                        "description": "Customizable Wayland desktop",
                    },
                    {
                        "value": "hyprland",
                        "label": "Hyprland",
                        "description": "Tiling Wayland compositor",
                    },
                    {"value": "none", "label": "None", "description": "Headless server / minimal"},
                ],
                hint="Select your desktop environment",
                allow_multiple=False,
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.CPU_SELECTION),
                    on_next=self._on_desktop_selected,
                ),
            )

        elif screen_id == ScreenId.SWAP_SELECTION:
            return SelectionScreen(
                self._root,
                title="SWAP SIZE",
                options=[
                    {"value": "8192", "label": "8 GB", "description": "Minimum for most systems"},
                    {
                        "value": "16384",
                        "label": "16 GB",
                        "description": "Recommended for 16GB+ RAM",
                    },
                    {"value": "32768", "label": "32 GB", "description": "For 32GB+ RAM systems"},
                    {"value": "0", "label": "No Swap", "description": "Not recommended"},
                ],
                hint="Select swap file size (hibernation requires swap >= RAM)",
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.DESKTOP_SELECTION),
                    on_next=self._on_swap_selected,
                ),
            )

        elif screen_id == ScreenId.FEATURE_SELECTION:
            return SelectionScreen(
                self._root,
                title="FEATURES",
                options=[
                    {
                        "value": "hibernation",
                        "label": "Hibernation",
                        "description": "Save session to disk on shutdown",
                    },
                    {
                        "value": "firewall",
                        "label": "UFW Firewall",
                        "description": "Simple firewall protection",
                    },
                    {
                        "value": "snapshots",
                        "label": "Bootable Snapshots",
                        "description": "Boot into previous states",
                    },
                ],
                hint="Select additional features (all recommended)",
                allow_multiple=True,
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.SWAP_SELECTION),
                    on_next=self._on_features_selected,
                ),
            )

        elif screen_id == ScreenId.CONFIRMATION:
            return ConfirmationScreen(
                self._root,
                config_summary=self._state.to_summary(),
                callbacks=ScreenCallbacks(
                    on_back=lambda: self._show_screen(ScreenId.FEATURE_SELECTION),
                    on_next=self._start_installation,
                ),
            )

        elif screen_id == ScreenId.PROGRESS:
            return ProgressScreen(
                self._root,
                callbacks=ScreenCallbacks(),
            )

        elif screen_id == ScreenId.COMPLETION:
            return CompletionScreen(
                self._root,
                success=True,
                callbacks=ScreenCallbacks(
                    on_select=self._on_completion_action,
                ),
            )

        return None

    def _get_default_disks(self) -> list[dict]:
        """return default disk list for testing."""
        return [
            {"path": "/dev/vda", "model": "QEMU Virtual Disk", "size": "20G"},
        ]

    # screen navigation callbacks

    def _on_welcome_select(self, option: str) -> None:
        if option == "exit":
            self._exit()
        elif option == "install":
            self._state.mode = "install"
            self._show_screen(ScreenId.DISK_SELECTION)
        elif option == "migrate":
            self._state.mode = "migrate"
            self._show_screen(ScreenId.DISK_SELECTION)
        elif option == "advanced":
            self._state.mode = "advanced"
            self._show_screen(ScreenId.DISK_SELECTION)

    def _on_disk_selected(self, path: str) -> None:
        self._state.target_disk = path

    def _on_disk_confirmed(self) -> None:
        self._show_screen(ScreenId.LUKS_PASSWORD)

    def _on_luks_password_submitted(self, data: dict) -> None:
        self._state.luks_password = data.get("password", "")
        self._show_screen(ScreenId.USER_PASSWORD)

    def _on_user_password_submitted(self, data: dict) -> None:
        self._state.user_password = data.get("password", "")
        self._show_screen(ScreenId.GPU_SELECTION)

    def _on_gpu_selected(self) -> None:
        self._show_screen(ScreenId.CPU_SELECTION)

    def _on_cpu_selected(self) -> None:
        self._show_screen(ScreenId.DESKTOP_SELECTION)

    def _on_desktop_selected(self) -> None:
        if isinstance(self._current_screen, SelectionScreen):
            selected = self._current_screen.get_selected()
            if "none" in selected:
                self._state.desktop_environments = []
            else:
                self._state.desktop_environments = selected
        self._show_screen(ScreenId.SWAP_SELECTION)

    def _on_swap_selected(self) -> None:
        if isinstance(self._current_screen, SelectionScreen):
            selected = self._current_screen.get_selected()
            if selected:
                self._state.swap_size_mb = int(selected[0])
        self._show_screen(ScreenId.FEATURE_SELECTION)

    def _on_features_selected(self) -> None:
        if isinstance(self._current_screen, SelectionScreen):
            selected = self._current_screen.get_selected()
            self._state.enable_hibernation = "hibernation" in selected
            self._state.enable_firewall = "firewall" in selected
            self._state.enable_snapshot_boot = "snapshots" in selected
        self._show_screen(ScreenId.CONFIRMATION)

    def _start_installation(self) -> None:
        self._show_screen(ScreenId.PROGRESS)
        if self._on_install:
            self._on_install(self._state)

    def _on_completion_action(self, action: str) -> None:
        if action == "exit":
            self._exit()
        elif action == "reboot":
            self._exit()

    # public methods for external control

    def get_state(self) -> InstallerState:
        """get the current installer state."""
        return self._state

    def get_progress_screen(self) -> Optional[ProgressScreen]:
        """get the progress screen for external updates."""
        if isinstance(self._current_screen, ProgressScreen):
            return self._current_screen
        return None

    def show_completion(self, success: bool = True, message: str = "") -> None:
        """show the completion screen."""
        if self._current_screen:
            self._current_screen.hide()
            self._current_screen.destroy()

        if self._root:
            self._current_screen = CompletionScreen(
                self._root,
                success=success,
                message=message,
                callbacks=ScreenCallbacks(
                    on_select=self._on_completion_action,
                ),
            )
            self._current_screen.show()


def run_gui(
    on_install: Optional[Callable[[InstallerState], None]] = None,
    disks: Optional[list[dict]] = None,
    fullscreen: bool = False,
) -> None:
    """convenience function to run the installer GUI.

    args:
        on_install: callback when installation is confirmed
        disks: list of available disk dicts
        fullscreen: whether to run in fullscreen mode
    """
    app = InstallerApp(on_install=on_install, fullscreen=fullscreen)
    if disks:
        app.set_disks(disks)
    app.run()
