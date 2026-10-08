"""the expectations `verify-install` checks the installed system against.

the installer writes them as shell assignments, so the verification script can `source`
them without a YAML parser on the target. values are shell-quoted and carry no secrets.
"""

import shlex

from arch_installer.config.models import InstallerConfig

EXPECTED_STATE_PATH = "/etc/dali/expected-state.env"


def _shell_word(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return shlex.quote(value)


def expected_state_values(config: InstallerConfig) -> dict[str, str | bool]:
    system, storage, gpu = config.system, config.storage, config.gpu
    return {
        "EXPECTED_HOSTNAME": system.hostname,
        "EXPECTED_TIMEZONE": system.timezone,
        "EXPECTED_LOCALE": system.locale.full_locale,
        "EXPECTED_KEYMAP": system.locale.keymap,
        "EXPECTED_USER": system.user.name,
        "EXPECTED_SWAP_ENABLED": storage.swap.enabled,
        "EXPECTED_SWAP_PATH": storage.swap.path,
        "EXPECTED_KERNEL_PACKAGES": " ".join(config.boot.kernel_packages),
        "EXPECTED_DISPLAY_MANAGERS": " ".join(config.packages.display_manager),
        "EXPECTED_SNAPPER_ENABLED": config.snapper.enabled,
        "EXPECTED_FIREWALL_ENABLED": config.firewall.enabled,
        "EXPECTED_GPU_VENDOR": str(gpu.vendor),
        "EXPECTED_GPU_DRIVER": str(gpu.driver),
        "EXPECTED_USB_BOOT": config.usb_boot.enabled,
        "EXPECTED_RECOVERY_SYSTEM": config.usb_boot.enabled and config.usb_boot.recovery_system,
    }


def expected_state_file(config: InstallerConfig) -> str:
    assignments = [
        f"{name}={_shell_word(value)}" for name, value in expected_state_values(config).items()
    ]
    header = "# written by the DALI installer from the final configuration, read by verify-install"
    return "\n".join([header, *assignments]) + "\n"
