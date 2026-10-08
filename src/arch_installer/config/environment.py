"""environment variables: their names, their parsing, and the settings they override.

the installer config is assembled in this order, later wins:
  1. defaults (models.py)
  2. config.yaml, with its encrypted passwords unlocked by the secrets key
  3. environment variables (Environment.override)
  4. TUI answers, which show the values from 1-3 as inherited
"""

from collections.abc import Mapping
from dataclasses import replace
from enum import StrEnum

from arch_installer.config.models import (
    BackupCategory,
    Desktop,
    InstallerConfig,
)
from arch_installer.core.secrets import decrypt_secret
from arch_installer.errors import ConfigurationError

TRUE_WORDS = ("true", "1", "yes")
FALSE_WORDS = ("false", "0", "no")


class EnvVar(StrEnum):
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
    SKIP_SWAP = "SKIP_SWAP"
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


class Environment:
    def __init__(self, variables: Mapping[str, str]) -> None:
        self._variables = variables

    def is_set(self, variable: EnvVar) -> bool:
        return bool(self._raw(variable))

    def text(self, variable: EnvVar, fallback: str = "") -> str:
        return self._raw(variable) or fallback

    def flag(self, variable: EnvVar, fallback: bool = False) -> bool:
        raw = self._raw(variable).lower()
        if not raw:
            return fallback
        if raw in TRUE_WORDS:
            return True
        if raw in FALSE_WORDS:
            return False
        raise ConfigurationError(f"{variable} must be true or false, got {raw!r}")

    def number(self, variable: EnvVar, fallback: int) -> int:
        raw = self._raw(variable)
        if not raw:
            return fallback
        if not raw.isdigit():
            raise ConfigurationError(f"{variable} must be a whole number, got {raw!r}")
        return int(raw)

    def choice[ChoiceT: StrEnum](self, variable: EnvVar, fallback: ChoiceT) -> ChoiceT:
        raw = self._raw(variable)
        if not raw:
            return fallback
        return self._parse_choice(variable, type(fallback), raw)

    def choices[ChoiceT: StrEnum](
        self, variable: EnvVar, choice_type: type[ChoiceT], fallback: tuple[ChoiceT, ...] | None
    ) -> tuple[ChoiceT, ...] | None:
        raw = self._raw(variable)
        if not raw:
            return fallback
        return tuple(self._parse_choice(variable, choice_type, item) for item in _split_list(raw))

    def names(self, variable: EnvVar, fallback: tuple[str, ...]) -> tuple[str, ...]:
        raw = self._raw(variable)
        return _split_list(raw) if raw else fallback

    def override(self, config: InstallerConfig) -> InstallerConfig:
        system, storage, swap = config.system, config.storage, config.storage.swap
        boot, packages, gpu = config.boot, config.packages, config.gpu
        usb_boot, credentials = config.usb_boot, config.credentials
        return replace(
            config,
            system=replace(system, cpu_vendor=self.choice(EnvVar.CPU_VENDOR, system.cpu_vendor)),
            storage=replace(
                storage,
                target_disk=self.text(EnvVar.TARGET_DISK, storage.target_disk),
                wipe_method=self.choice(EnvVar.WIPE_METHOD, storage.wipe_method),
                swap=replace(
                    swap,
                    enabled=not self.flag(EnvVar.SKIP_SWAP, not swap.enabled),
                    size_mb=self.number(EnvVar.SWAP_SIZE_MB, swap.size_mb),
                    hibernation=self.flag(EnvVar.ENABLE_HIBERNATION, swap.hibernation),
                ),
            ),
            boot=replace(
                boot,
                selected_kernels=self.names(EnvVar.SELECTED_KERNELS, boot.selected_kernels),
                enable_snapshot_boot=self.flag(
                    EnvVar.ENABLE_SNAPSHOT_BOOT, boot.enable_snapshot_boot
                ),
            ),
            packages=replace(
                packages,
                selected_desktops=self.choices(
                    EnvVar.SELECTED_DESKTOPS, Desktop, packages.selected_desktops
                ),
            ),
            gpu=replace(
                gpu,
                vendor=self.choice(EnvVar.GPU_VENDOR, gpu.vendor),
                driver=self.choice(EnvVar.GPU_DRIVER, gpu.driver),
            ),
            firewall=replace(
                config.firewall,
                enabled=self.flag(EnvVar.ENABLE_FIREWALL, config.firewall.enabled),
            ),
            docker=replace(
                config.docker, enabled=self.flag(EnvVar.ENABLE_DOCKER, config.docker.enabled)
            ),
            notifications=replace(
                config.notifications,
                enabled=self.flag(EnvVar.ENABLE_NOTIFICATIONS, config.notifications.enabled),
            ),
            migration=replace(
                config.migration,
                enabled=self.flag(EnvVar.ENABLE_MIGRATION, config.migration.enabled),
            ),
            usb_boot=replace(
                usb_boot,
                enabled=self.flag(EnvVar.ENABLE_USB_BOOT, usb_boot.enabled),
                device=self.text(EnvVar.USB_BOOT_DEVICE, usb_boot.device),
                iso_path=self.text(EnvVar.ISO_PATH, usb_boot.iso_path),
            ),
            sync=replace(
                config.sync,
                backup_categories=self.choices(
                    EnvVar.BACKUP_CATEGORIES, BackupCategory, config.sync.backup_categories
                ),
            ),
            credentials=replace(
                credentials,
                luks_password=self.text(EnvVar.LUKS_PASSWORD, credentials.luks_password),
                user_password=self.text(EnvVar.USER_PASSWORD, credentials.user_password),
                source_luks_password=self.text(
                    EnvVar.SOURCE_LUKS_PASSWORD, credentials.source_luks_password
                ),
            ),
        )

    def _raw(self, variable: EnvVar) -> str:
        return self._variables.get(variable, "").strip()

    @staticmethod
    def _parse_choice[ChoiceT: StrEnum](
        variable: EnvVar, choice_type: type[ChoiceT], raw: str
    ) -> ChoiceT:
        try:
            return choice_type(raw.lower())
        except ValueError:
            allowed = ", ".join(member.value for member in choice_type if member.value)
            raise ConfigurationError(f"{variable}={raw!r} is not one of: {allowed}") from None


def _split_list(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def unlock_secrets(config: InstallerConfig, secrets_key: str) -> InstallerConfig:
    secrets, credentials = config.secrets, config.credentials
    if secrets.luks_password_encrypted:
        credentials = replace(
            credentials, luks_password=decrypt_secret(secrets.luks_password_encrypted, secrets_key)
        )
    if secrets.user_password_encrypted:
        credentials = replace(
            credentials, user_password=decrypt_secret(secrets.user_password_encrypted, secrets_key)
        )
    return replace(config, credentials=credentials)
