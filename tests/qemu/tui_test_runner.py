"""standalone TUI runner for the QEMU e2e test.

assembles the configuration exactly like the installer does (environment over config.yaml,
then the TUI) and dumps it to JSON so the test can assert that the driven keystrokes
produced the expected selections.
"""

import json
import sys

sys.path.insert(0, "/root/arch_installer/src")

from arch_installer.cli import assemble_installer_config
from arch_installer.config.environment import Environment
from arch_installer.tui.app import run_tui_setup


def selections_from_config(config) -> dict:
    return {
        "hostname": config.system.hostname,
        "username": config.system.user.name,
        "timezone": config.system.timezone,
        "keymap": config.system.locale.keymap,
        "luks_password": config.credentials.luks_password,
        "user_password": config.credentials.user_password,
        "target_disk": config.storage.target_disk,
        "wipe_method": str(config.storage.wipe_method),
        "cpu_vendor": str(config.system.cpu_vendor),
        "gpu_vendor": str(config.gpu.vendor),
        "gpu_driver": str(config.gpu.driver),
        "swap_size_mb": config.storage.swap.size_mb if config.storage.swap.enabled else 0,
        "enable_migration": config.migration.enabled,
        "enable_hibernation": config.storage.swap.hibernation,
        "enable_firewall": config.firewall.enabled,
        "enable_snapshot_boot": config.boot.enable_snapshot_boot,
        "enable_docker": config.docker.enabled,
        "selected_desktops": [str(desktop) for desktop in config.packages.selected_desktops],
    }


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "/root/arch_installer/config/config.yaml"
    config = assemble_installer_config(Environment({"CONFIG_PATH": config_path}), tui=run_tui_setup)
    with open("/tmp/tui_selections.json", "w") as handle:
        json.dump(selections_from_config(config), handle, indent=2)
    print("TUI_SELECTIONS_SAVED")


if __name__ == "__main__":
    main()
