"""Tkinter-based GUI for the Arch Installer."""

import os
import sys
import threading

from arch_installer.config.loader import load_main_yaml_config
from arch_installer.core.command import SystemCommandRunner
from arch_installer.core.runtime_state import create_default_runtime_config
from arch_installer.gui.app import InstallerApp, InstallerState
from arch_installer.gui.theme import CyberpunkTheme
from arch_installer.installer import ArchInstaller

__all__ = ["InstallerApp", "InstallerState", "CyberpunkTheme", "main", "run_gui_installer"]


def _check_display() -> None:
    """check if DISPLAY environment variable is set for X11/Wayland."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("GUI Error: no display name and no $DISPLAY environment variable", file=sys.stderr)
        print("Hint: Run 'make install' for CLI mode, or set DISPLAY for GUI.", file=sys.stderr)
        sys.exit(1)


def run_gui_installer() -> int:
    """run the GUI installer and return exit code."""
    _check_display()

    def perform_installation(state: InstallerState, app: InstallerApp) -> None:
        """run the actual installation based on GUI selections."""

        def install_thread():
            try:
                config_path = os.environ.get("ARCH_INSTALLER_CONFIG", "config/config.yaml")
                config = load_main_yaml_config(config_path)
                runner = SystemCommandRunner()
                runtime_state = create_default_runtime_config()

                # populate runtime config directly from GUI state
                runtime_state.non_interactive = True
                runtime_state.target_disk = state.target_disk
                runtime_state.luks_password = state.luks_password
                runtime_state.user_password = state.user_password
                runtime_state.cpu_vendor = state.cpu_vendor
                runtime_state.gpu_vendor = state.gpu_vendor
                runtime_state.swap_size_mb = state.swap_size_mb
                runtime_state.enable_hibernation = state.enable_hibernation
                runtime_state.enable_firewall = state.enable_firewall
                runtime_state.enable_snapshot_boot = state.enable_snapshot_boot
                runtime_state.selected_desktops = (
                    list(state.desktop_environments) if state.desktop_environments else []
                )

                installer = ArchInstaller(config, runtime_state, runner)
                installer.install()
                _on_install_complete(app, True)
            except Exception as e:
                _on_install_complete(app, False, str(e))

        thread = threading.Thread(target=install_thread, daemon=True)
        thread.start()

    def _on_install_complete(app: InstallerApp, success: bool, message: str = "") -> None:
        """called on main thread when installation completes."""
        auto_exit = os.environ.get("ARCH_INSTALLER_AUTO_EXIT", "0") == "1"

        def update_ui():
            if success:
                if auto_exit:
                    app._exit()
                else:
                    app.show_completion(success=True, message="Installation complete!")
            else:
                if auto_exit:
                    app._exit()
                else:
                    app.show_completion(success=False, message=message)

        if app._root:
            app._root.after(0, update_ui)

    try:
        app = InstallerApp()
        app._on_install = lambda state: perform_installation(state, app)
        app.run()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"GUI Error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """entry point for GUI installer."""
    return run_gui_installer()
