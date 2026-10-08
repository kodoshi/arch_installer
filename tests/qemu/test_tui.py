"""QEMU e2e tests for the TUI interactive installer.

runs the curses TUI in a tmux session on the VM, sends keystrokes to navigate menus
and make selections, then asserts that the collected selections match what was driven.
"""

import json
from pathlib import Path

import pytest

from tests.qemu.test_installation import QEMU_DATA_DIRECTORY, run_checked, setup_vm_for_install
from tests.qemu.tmux_driver import INSTALL_TMUX_IF_MISSING, TmuxScreenInput, TmuxSession
from tests.qemu.vm import QemuVm

# screens follow the install steps; inherited values come from maximal_config.yaml (no
# encrypted passwords, so they are typed); comments give moves from the inherited option.
# a switch lists Off before On
TUI_SESSION = (
    TmuxScreenInput("DALI", ("Enter",)),
    TmuxScreenInput("Migration staging: Installation type", ("Enter",)),  # fresh
    TmuxScreenInput("USB boot drive: USB boot drive", ("Up", "Enter")),  # yes -> no
    TmuxScreenInput("Storage: Disk", ("Enter",)),  # /dev/vda (inherited, only disk)
    TmuxScreenInput("Storage: Wipe method", ("Down", "Enter")),  # quick -> secure
    TmuxScreenInput("Storage: Swap file", ("Enter",)),  # on
    TmuxScreenInput("Storage: Swap size", ("Down", "Down", "Down", "Enter")),  # 1 GB -> 16 GB
    TmuxScreenInput("Storage: Hibernation", ("Up", "Enter")),  # on -> off
    TmuxScreenInput("Storage: LUKS password", ("testluks123", "Enter")),
    TmuxScreenInput("Storage: LUKS password", ("testluks123", "Enter")),  # confirmation
    TmuxScreenInput("Packages: CPU vendor", ("Up", "Enter")),  # amd -> intel
    TmuxScreenInput("Packages: GPU vendor", ("Up", "Enter")),  # none -> nvidia
    # the inherited driver is the vendor default, so nothing is preselected: nouveau -> open
    TmuxScreenInput("Packages: NVIDIA driver", ("Down", "Enter")),
    # all three inherited as selected, untick hyprland
    TmuxScreenInput("Packages: Desktops", ("Down", "Down", "Space", "Enter")),
    TmuxScreenInput("System: Hostname", ("tui-test-host", "Enter")),
    TmuxScreenInput("System: Username", ("tuiuser", "Enter")),
    TmuxScreenInput("System: Timezone", ("Enter",)),  # keep Europe/Paris
    TmuxScreenInput("System: Keymap", ("Enter",)),  # keep us
    TmuxScreenInput("System: User password", ("testuser456", "Enter")),
    TmuxScreenInput("System: User password", ("testuser456", "Enter")),  # confirmation
    TmuxScreenInput("Docker: Docker", ("Up", "Enter")),  # on -> off
    TmuxScreenInput("Bootable snapshots: Bootable snapshots", ("Enter",)),  # on
    TmuxScreenInput("Snapshot notifications: Desktop notifications", ("Enter",)),  # on
    TmuxScreenInput("Firewall: Firewall (UFW)", ("Enter",)),  # on
    TmuxScreenInput("Configuration Summary", ("y",)),
)

EXPECTED_SELECTIONS = {
    "hostname": "tui-test-host",
    "username": "tuiuser",
    "timezone": "Europe/Paris",
    "keymap": "us",
    "luks_password": "testluks123",
    "user_password": "testuser456",
    "target_disk": "/dev/vda",
    "wipe_method": "secure",
    "cpu_vendor": "intel",
    "gpu_vendor": "nvidia",
    "gpu_driver": "nvidia-open",
    "swap_size_mb": 16384,
    "enable_migration": False,
    "enable_hibernation": False,
    "enable_firewall": True,
    "enable_snapshot_boot": True,
    "enable_docker": False,
}
EXPECTED_DESKTOPS = ["gnome", "kde"]


@pytest.mark.timeout(600)
class TestTuiInteraction:
    @pytest.mark.qemu
    @pytest.mark.slow
    def test_tui_navigation_and_selection_produces_expected_config(
        self,
        qemu_vm_with_network: QemuVm,
    ) -> None:
        vm = qemu_vm_with_network

        print("\n=== phase 1: setup VM for TUI test ===")
        setup_vm_for_install(vm, config_path=QEMU_DATA_DIRECTORY / "maximal_config.yaml")
        run_checked(vm, [INSTALL_TMUX_IF_MISSING], timeout=120)
        vm.copy_file_to_vm(Path(__file__).parent / "tui_test_runner.py", "/root/tui_test_runner.py")

        print("\n=== phase 2: drive TUI in tmux ===")
        tui = TmuxSession(vm, "tui")
        tui.start(
            "PYTHONPATH=/root/arch_installer/src "
            "python /root/tui_test_runner.py /root/arch_installer/config/config.yaml"
        )
        for screen in TUI_SESSION:
            tui.drive(screen)
        tui_exit = tui.wait_for_exit(timeout=60, poll_seconds=2)
        assert tui_exit == 0, f"TUI runner failed with exit code {tui_exit}"

        print("\n=== phase 3: verify selections ===")
        exit_code, stdout, stderr = vm.run_ssh_command("cat /tmp/tui_selections.json", timeout=10)
        assert exit_code == 0, f"Failed to read selections: {stderr}"
        selections = json.loads(stdout)

        mismatches = {
            name: {"expected": expected, "actual": selections.get(name)}
            for name, expected in EXPECTED_SELECTIONS.items()
            if selections.get(name) != expected
        }
        assert not mismatches, f"TUI selections differ from the driven input: {mismatches}"
        assert sorted(selections["selected_desktops"]) == EXPECTED_DESKTOPS
