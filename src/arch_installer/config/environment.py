"""environment variables: their names, how their text is read, and which setting each one
provides.

a variable that is set provides its setting's value; an unset variable provides nothing.
how that value ranks against config.yaml and the TUI is decided in value_precedence.py.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from arch_installer.config.models import (
    BackupCategory,
    CpuVendor,
    Desktop,
    GpuDriver,
    GpuVendor,
    WipeMethod,
)
from arch_installer.errors import ConfigurationError

TRUE_WORDS = ("true", "1", "yes")
FALSE_WORDS = ("false", "0", "no")


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
    ISO_PATH = "ISO_PATH"
    BACKUP_CATEGORIES = "BACKUP_CATEGORIES"
    CPU_VENDOR = "CPU_VENDOR"
    GPU_VENDOR = "GPU_VENDOR"
    GPU_DRIVER = "GPU_DRIVER"
    SELECTED_KERNELS = "SELECTED_KERNELS"
    SELECTED_DESKTOPS = "SELECTED_DESKTOPS"


def _split_list(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def read_text(variable: EnvVariable, raw: str) -> str:
    return raw


def read_flag(variable: EnvVariable, raw: str) -> bool:
    if raw.lower() in TRUE_WORDS:
        return True
    if raw.lower() in FALSE_WORDS:
        return False
    raise ConfigurationError(f"{variable} must be true or false, got {raw!r}")


def read_number(variable: EnvVariable, raw: str) -> int:
    if not raw.isdigit():
        raise ConfigurationError(f"{variable} must be a whole number, got {raw!r}")
    return int(raw)


def read_names(variable: EnvVariable, raw: str) -> tuple[str, ...]:
    return _split_list(raw)


def _parse_choice[ChoiceT: StrEnum](
    variable: EnvVariable, choice_type: type[ChoiceT], raw: str
) -> ChoiceT:
    try:
        return choice_type(raw.lower())
    except ValueError:
        allowed = ", ".join(member.value for member in choice_type if member.value)
        raise ConfigurationError(f"{variable}={raw!r} is not one of: {allowed}") from None


def read_choice(choice_type: type[StrEnum]) -> Callable[[EnvVariable, str], StrEnum]:
    return lambda variable, raw: _parse_choice(variable, choice_type, raw)


def read_choices(
    choice_type: type[StrEnum],
) -> Callable[[EnvVariable, str], tuple[StrEnum, ...]]:
    return lambda variable, raw: tuple(
        _parse_choice(variable, choice_type, item) for item in _split_list(raw)
    )


@dataclass(frozen=True)
class EnvironmentSetting:
    variable: EnvVariable
    setting_path: str
    read: Callable[[EnvVariable, str], Any]


# every setting an environment variable can provide, and how the variable's text is read
ENVIRONMENT_SETTINGS = (
    EnvironmentSetting(EnvVariable.LUKS_PASSWORD, "credentials.luks_password", read_text),
    EnvironmentSetting(EnvVariable.USER_PASSWORD, "credentials.user_password", read_text),
    EnvironmentSetting(
        EnvVariable.SOURCE_LUKS_PASSWORD, "credentials.source_luks_password", read_text
    ),
    EnvironmentSetting(EnvVariable.TARGET_DISK, "storage.target_disk", read_text),
    EnvironmentSetting(EnvVariable.WIPE_METHOD, "storage.wipe_method", read_choice(WipeMethod)),
    EnvironmentSetting(EnvVariable.SWAP_SIZE_MB, "storage.swap.size_mb", read_number),
    EnvironmentSetting(EnvVariable.ENABLE_SWAP, "storage.swap.enabled", read_flag),
    EnvironmentSetting(EnvVariable.ENABLE_HIBERNATION, "storage.swap.hibernation", read_flag),
    EnvironmentSetting(EnvVariable.ENABLE_SNAPSHOT_BOOT, "boot.enable_snapshot_boot", read_flag),
    EnvironmentSetting(EnvVariable.SELECTED_KERNELS, "boot.selected_kernels", read_names),
    EnvironmentSetting(
        EnvVariable.SELECTED_DESKTOPS, "packages.selected_desktops", read_choices(Desktop)
    ),
    EnvironmentSetting(EnvVariable.CPU_VENDOR, "system.cpu_vendor", read_choice(CpuVendor)),
    EnvironmentSetting(EnvVariable.GPU_VENDOR, "gpu.vendor", read_choice(GpuVendor)),
    EnvironmentSetting(EnvVariable.GPU_DRIVER, "gpu.driver", read_choice(GpuDriver)),
    EnvironmentSetting(EnvVariable.ENABLE_FIREWALL, "firewall.enabled", read_flag),
    EnvironmentSetting(EnvVariable.ENABLE_DOCKER, "docker.enabled", read_flag),
    EnvironmentSetting(EnvVariable.ENABLE_NOTIFICATIONS, "notifications.enabled", read_flag),
    EnvironmentSetting(EnvVariable.ENABLE_MIGRATION, "migration.enabled", read_flag),
    EnvironmentSetting(EnvVariable.ENABLE_USB_BOOT, "usb_boot.enabled", read_flag),
    EnvironmentSetting(EnvVariable.USB_BOOT_DEVICE, "usb_boot.device", read_text),
    EnvironmentSetting(EnvVariable.ISO_PATH, "usb_boot.iso_path", read_text),
    EnvironmentSetting(
        EnvVariable.BACKUP_CATEGORIES, "sync.backup_categories", read_choices(BackupCategory)
    ),
)


def variable_for_setting(setting_path: str) -> EnvVariable | None:
    return next(
        (
            setting.variable
            for setting in ENVIRONMENT_SETTINGS
            if setting.setting_path == setting_path
        ),
        None,
    )


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
        return self.is_set(variable) and read_flag(variable, self._raw(variable))

    def setting_values(self) -> dict[str, Any]:
        return {
            setting.setting_path: setting.read(setting.variable, self._raw(setting.variable))
            for setting in ENVIRONMENT_SETTINGS
            if self.is_set(setting.variable)
        }
