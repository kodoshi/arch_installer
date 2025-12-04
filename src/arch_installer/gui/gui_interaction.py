"""
GUI implementation of the interaction strategy.

provides tkinter-based GUI screens for all user input during installation.
implements the same InteractionStrategy interface as CLI prompts.
"""

import threading
from typing import Optional

from arch_installer.core.command import CommandRunner
from arch_installer.core.interaction import InstallationSelections, InteractionStrategy
from arch_installer.gui.app import InstallerApp, InstallerState


def _state_to_selections(state: InstallerState) -> InstallationSelections:
    """convert GUI InstallerState to InstallationSelections."""
    return InstallationSelections(
        target_disk=state.target_disk,
        luks_password=state.luks_password,
        user_password=state.user_password,
        gpu_vendor=state.gpu_vendor,
        gpu_driver=state.gpu_driver,
        cpu_vendor=state.cpu_vendor,
        selected_desktops=state.desktop_environments or [],
        swap_size_mb=state.swap_size_mb,
        wipe_method=getattr(state, "wipe_method", "quick"),
        enable_hibernation=state.enable_hibernation,
        enable_firewall=state.enable_firewall,
        enable_snapshot_boot=state.enable_snapshot_boot,
    )


class GUIInteractionStrategy(InteractionStrategy):
    """GUI implementation using tkinter screens.

    unlike CLI which prompts sequentially, GUI collects all input
    through screen navigation before returning.
    """

    def __init__(self, runner: CommandRunner | None = None) -> None:
        super().__init__(runner)
        self._app: Optional[InstallerApp] = None
        self._result: Optional[InstallationSelections] = None
        self._completed = threading.Event()

    def collect_all_selections(
        self,
        require_disk: bool = True,
        require_passwords: bool = True,
        require_system_config: bool = False,
    ) -> InstallationSelections:
        """run GUI to collect all installation selections.

        this blocks until user completes the GUI flow or exits.
        """
        self._completed.clear()
        self._result = None

        def on_install(state: InstallerState) -> None:
            self._result = _state_to_selections(state)
            self._completed.set()
            if self._app and self._app._root:
                self._app._root.after(0, self._app._exit)

        self._app = InstallerApp(on_install=on_install)

        # set available disks from hardware detection
        disks = self.get_available_disks()
        disk_dicts = [{"path": d.path, "model": d.model, "size": d.size} for d in disks]
        self._app.set_disks(disk_dicts)

        self._app.run()

        if self._result is None:
            raise SystemExit("GUI cancelled by user")

        return self._result

    def prompt_disk_selection(self) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_password(self, password_type: str, confirm: bool = True) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_gpu_vendor(self) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_cpu_vendor(self) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_desktop_environment(self) -> list[str]:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_swap_size(self) -> int:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_wipe_method(self) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_boolean(self, prompt_text: str, default: bool = False) -> bool:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_text(
        self,
        prompt_text: str,
        default: str = "",
        required: bool = False,
    ) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_secrets_key(self) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")

    def prompt_nvidia_driver(self) -> str:
        """not directly supported - use collect_all_selections."""
        raise NotImplementedError("GUI collects all input via collect_all_selections")
