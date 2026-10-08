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

# inherited values come from maximal_config.yaml (no encrypted passwords, so they are
# typed); comments give cursor moves relative to the inherited option
TUI_SESSION = (
    TmuxScreenInput("DALI", ("Enter",)),
    TmuxScreenInput("Installation Type", ("Enter",)),  # fresh install (inherited)
    TmuxScreenInput("System Configuration", ("tui-test-host", "Enter")),  # hostname
    TmuxScreenInput("System Configuration", ("tuiuser", "Enter")),  # username
    TmuxScreenInput("System Configuration", ("Enter",)),  # timezone: keep Europe/Paris
    TmuxScreenInput("System Configuration", ("Enter",)),  # keymap: keep us
    TmuxScreenInput("Password Setup", ("testluks123", "Enter")),  # LUKS password
    TmuxScreenInput("Password Setup", ("testluks123", "Enter")),  # confirmation
    TmuxScreenInput("Password Setup", ("testuser456", "Enter")),  # user password
    TmuxScreenInput("Password Setup", ("testuser456", "Enter")),  # confirmation
    TmuxScreenInput("Disk Selection", ("Enter",)),  # /dev/vda (inherited, only disk)
    TmuxScreenInput("Disk Wipe Method", ("Down", "Enter")),  # quick -> secure
    TmuxScreenInput("USB Boot", ("Up", "Enter")),  # yes -> no
    TmuxScreenInput("CPU Vendor", ("Up", "Enter")),  # amd -> intel
    TmuxScreenInput("GPU Vendor", ("Up", "Enter")),  # none -> nvidia
    # the inherited driver is the vendor default, so nothing is preselected: nouveau -> nvidia-open
    TmuxScreenInput("NVIDIA Driver", ("Down", "Enter")),
    # all three inherited as selected, untick hyprland
    TmuxScreenInput("Desktop Environments", ("Down", "Down", "Space", "Enter")),
    TmuxScreenInput("Swap Size", ("Down", "Down", "Down", "Enter")),  # 1 GB -> 16 GB
    # hibernation off, docker off
    TmuxScreenInput("Features", ("Space", "Down", "Down", "Down", "Space", "Tab")),
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
