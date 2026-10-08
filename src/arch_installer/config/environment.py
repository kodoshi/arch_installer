"""environment variables: their names, and reading the ones that are set.

which variable provides which setting is declared with the install steps (see
install_steps/registry.py); a set variable's text is read as the type the model
declares for that setting. how a value ranks against config.yaml and the TUI is
decided in value_precedence.py.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from arch_installer.config.installer_config_builder import (
    FALSE_WORDS,
    TRUE_WORDS,
    read_setting_text,
)
from arch_installer.errors import ConfigurationError


class EnvVariable(StrEnum):
    CONFIG_PATH = "CONFIG_PATH"
    NON_INTERACTIVE = "NON_INTERACTIVE"
    VERBOSE = "VERBOSE"
    NO_WRITE = "NO_WRITE"
    SECRETS_KEY = "ARCH_INSTALLER_SECRETS_KEY"
    LUKS_PASSWORD = "LUKS_PASSWORD"
    USER_PASSWORD = "USER_PASSWORD"
    SOURCE_LUKS_PASSWORD = "SOURCE_LUKS_PASSWORD"
    TARGET_DISK = "TARGET_DISK"
    WIPE_METHOD = "WIPE_METHOD"
    SWAP_SIZE_MB = "SWAP_SIZE_MB"
    ENABLE_SWAP = "ENABLE_SWAP"
    ENABLE_HIBERNATION = "ENABLE_HIBERNATION"
    ENABLE_SNAPSHOT_BOOT = "ENABLE_SNAPSHOT_BOOT"
    ENABLE_FIREWALL = "ENABLE_FIREWALL"
    ENABLE_DOCKER = "ENABLE_DOCKER"
    ENABLE_NOTIFICATIONS = "ENABLE_NOTIFICATIONS"
    ENABLE_MIGRATION = "ENABLE_MIGRATION"
    ENABLE_USB_BOOT = "ENABLE_USB_BOOT"
    USB_BOOT_DEVICE = "USB_BOOT_DEVICE"
    ENABLE_RECOVERY_SYSTEM = "ENABLE_RECOVERY_SYSTEM"
    ISO_PATH = "ISO_PATH"
    SPARE_USB_DEVICE = "SPARE_USB_DEVICE"
    BACKUP_PARTITION = "BACKUP_PARTITION"
    BACKUP_CATEGORIES = "BACKUP_CATEGORIES"
    CPU_VENDOR = "CPU_VENDOR"
    GPU_VENDOR = "GPU_VENDOR"
    GPU_DRIVER = "GPU_DRIVER"
    SELECTED_KERNELS = "SELECTED_KERNELS"
    SELECTED_DESKTOPS = "SELECTED_DESKTOPS"


class Environment:
    def __init__(self, variables: Mapping[str, str]) -> None:
        self._variables = variables

    def _raw(self, variable: EnvVariable) -> str:
        return self._variables.get(variable, "").strip()

    def is_set(self, variable: EnvVariable) -> bool:
        return bool(self._raw(variable))

    # the variables that are not settings: paths, keys and switches of the tools
    def text(self, variable: EnvVariable) -> str:
        return self._raw(variable)

    def switch_is_on(self, variable: EnvVariable) -> bool:
        raw = self._raw(variable).lower()
        if not raw or raw in FALSE_WORDS:
            return False
        if raw in TRUE_WORDS:
            return True
        raise ConfigurationError(f"{variable} must be true or false, got {raw!r}")

    # variable_paths maps each variable that provides a setting to that setting's path
    def setting_values(self, variable_paths: Mapping[EnvVariable, str]) -> dict[str, Any]:
        return {
            setting_path: read_setting_text(setting_path, self._raw(variable), variable)
            for variable, setting_path in variable_paths.items()
            if self.is_set(variable)
        }
