"""Command-line entry points: arch-installer, clone-usb-boot, usb-backup and the
secrets commands behind make encrypt-secrets and make decrypt-secrets.
"""

import getpass
import logging
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from arch_installer.config.config_file import (
    REPOSITORY_CONFIG_PATH,
    config_file_setting_values,
    decrypted_credentials,
    has_encrypted_credentials,
    read_config_file,
)
from arch_installer.config.environment import Environment, EnvVariable
from arch_installer.config.installer_config_builder import (
    MissingSettingsError,
    build_installer_config,
)
from arch_installer.config.models import (
    LUKS_PASSWORD_SECRET,
    USER_PASSWORD_SECRET,
    EncryptedSecretsConfig,
    InstallerConfig,
    WipeMethod,
)
from arch_installer.config.secrets_file import write_encrypted_secrets
from arch_installer.config.value_precedence import (
    SettingValue,
    ValueSource,
    apply_tui_choices,
    inherit_setting_values,
    plain_values,
)
from arch_installer.core import log
from arch_installer.core.command import SystemCommandRunner
from arch_installer.core.secrets import encrypt_secret
from arch_installer.errors import ArchInstallerError, ConfigurationError
from arch_installer.executors.usb_backup import UsbBackupStepExecutor
from arch_installer.executors.usb_boot import UsbBootDriveCloner
from arch_installer.install_steps.registry import environment_variable_paths, variable_for_setting
from arch_installer.installer import Installer
from arch_installer.tui.curses_frontend import run_tui_setup

logger = logging.getLogger(log.PACKAGE_LOGGER_NAME)


def _log_level(environment: Environment) -> int:
    verbose = environment.text(EnvVariable.VERBOSE).lower()
    if verbose == "true":
        return logging.DEBUG
    if verbose == "quiet":
        return logging.WARNING
    return logging.INFO


# the interactive step: shown the inherited values, returns the values the user chose
TuiSetup = Callable[[Mapping[str, SettingValue]], Mapping[str, Any]]


def _is_interactive(environment: Environment) -> bool:
    return not environment.switch_is_on(EnvVariable.NON_INTERACTIVE)


def _config_path(environment: Environment) -> Path:
    return Path(environment.text(EnvVariable.CONFIG_PATH) or REPOSITORY_CONFIG_PATH)


def assemble_installer_config(environment: Environment, tui: TuiSetup | None) -> InstallerConfig:
    environment_values = environment.setting_values(environment_variable_paths())
    config_file_values = config_file_setting_values(read_config_file(_config_path(environment)))
    config_file_values |= _unlocked_passwords(
        config_file_values, environment_values, environment, tui
    )

    inherited = inherit_setting_values(
        {
            ValueSource.ENVIRONMENT: environment_values,
            ValueSource.CONFIG_FILE: config_file_values,
        }
    )
    final_values = inherited if tui is None else apply_tui_choices(inherited, tui(inherited))
    _log_value_sources(final_values)
    try:
        return build_installer_config(plain_values(final_values))
    except MissingSettingsError as error:
        raise ConfigurationError(_explain_missing(error.setting_paths)) from error


# the passwords encrypted in config.yaml are only unlocked when the environment does not
# already provide both of them; the key is asked for when it is not in the environment
def _unlocked_passwords(
    config_file_values: Mapping[str, Any],
    environment_values: Mapping[str, Any],
    environment: Environment,
    tui: TuiSetup | None,
) -> dict[str, str]:
    credentials_from_environment = {
        "credentials.luks_password",
        "credentials.user_password",
    } <= environment_values.keys()
    if not has_encrypted_credentials(config_file_values) or credentials_from_environment:
        return {}

    secrets_key = environment.text(EnvVariable.SECRETS_KEY)
    if not secrets_key and tui is not None:
        secrets_key = _ask_secret("Secrets decryption key", confirm=False)
    if not secrets_key:
        return {}
    return decrypted_credentials(config_file_values, secrets_key)


def _log_value_sources(final_values: Mapping[str, SettingValue]) -> None:
    for source in (ValueSource.ENVIRONMENT, ValueSource.TUI):
        paths = sorted(path for path, setting in final_values.items() if setting.source == source)
        if paths:
            logger.info("Settings from the %s: %s", source, ", ".join(paths))


def _explain_missing(setting_paths: list[str]) -> str:
    lines = []
    for setting_path in setting_paths:
        variable = variable_for_setting(setting_path)
        alternative = f" or {variable}" if variable else ""
        lines.append(f"{setting_path} (config.yaml{alternative})")
    return "No source provides these settings:\n  - " + "\n  - ".join(lines)


def validate_for_install(config: InstallerConfig) -> None:
    problems = []
    if not config.credentials.luks_password:
        problems.append(f"no LUKS password ({EnvVariable.LUKS_PASSWORD} or encrypted secrets)")
    if not config.credentials.user_password:
        problems.append(f"no user password ({EnvVariable.USER_PASSWORD} or encrypted secrets)")
    if not config.storage.target_disk:
        problems.append(f"no target disk ({EnvVariable.TARGET_DISK} or storage.target_disk)")
    if config.migration.enabled and not config.credentials.source_luks_password:
        problems.append(
            f"migration needs the old LUKS password ({EnvVariable.SOURCE_LUKS_PASSWORD})"
        )
    if config.usb_boot.enabled:
        problems.extend(_usb_boot_problems(config))
    if problems:
        raise ConfigurationError("Cannot start the installation:\n  - " + "\n  - ".join(problems))


def _usb_boot_problems(config: InstallerConfig) -> list[str]:
    usb_boot = config.usb_boot
    problems = []
    if not usb_boot.device:
        problems.append(
            f"USB boot needs a drive ({EnvVariable.USB_BOOT_DEVICE} or usb_boot.device)"
        )
    elif usb_boot.device == config.storage.target_disk:
        problems.append(f"the USB boot drive and the target disk are both {usb_boot.device}")
    if usb_boot.recovery_system and not usb_boot.iso_path:
        problems.append(
            f"the recovery system needs an Arch ISO ({EnvVariable.ISO_PATH} or usb_boot.iso_path)"
        )
    # migration always fills the disk with random data when the header is detached
    if not config.migration.enabled and config.storage.wipe_method in (
        WipeMethod.QUICK,
        WipeMethod.DISCARD,
    ):
        problems.append(
            "ciphertext without a header only hides on a disk filled with random data: "
            f"{EnvVariable.WIPE_METHOD} secure, or skip for a disk filled before"
        )
    return problems


def _run(entry: Callable[[Environment], None], variables: Mapping[str, str]) -> int:
    environment = Environment(variables)
    log.configure_logging(_log_level(environment))
    try:
        entry(environment)
        return 0
    except KeyboardInterrupt:
        logger.warning("Cancelled by user")
        return 130
    except ArchInstallerError as error:
        logger.error(str(error))
        return 1


def _install(environment: Environment) -> None:
    tui = run_tui_setup if _is_interactive(environment) else None
    config = assemble_installer_config(environment, tui)
    validate_for_install(config)
    Installer(config, SystemCommandRunner()).install()


def _clone_usb_boot(environment: Environment) -> None:
    source_device = environment.text(EnvVariable.USB_BOOT_DEVICE)
    spare_device = environment.text(EnvVariable.SPARE_USB_DEVICE)
    if not (source_device and spare_device):
        raise ConfigurationError(
            f"Cloning needs the USB boot drive ({EnvVariable.USB_BOOT_DEVICE}) "
            f"and the spare drive ({EnvVariable.SPARE_USB_DEVICE})"
        )
    UsbBootDriveCloner(SystemCommandRunner()).clone(source_device, spare_device)


def _usb_backup(environment: Environment) -> None:
    config = assemble_installer_config(environment, tui=None)
    UsbBackupStepExecutor(config, SystemCommandRunner()).execute()


# typed without echo, so the secret never reaches `ps` output or the shell history
def _ask_secret(label: str, confirm: bool) -> str:
    try:
        while True:
            secret = getpass.getpass(f"{label}: ")
            if not confirm or not secret:
                return secret
            if getpass.getpass(f"{label} (again): ") == secret:
                return secret
            logger.warning("The two entries differ, try again")
    except EOFError as error:
        raise ConfigurationError(f"{label} is needed but there is no terminal to ask on") from error


def _secret_from_environment_or_prompt(
    environment: Environment, variable: EnvVariable, label: str, confirm: bool
) -> str:
    return environment.text(variable) or _ask_secret(label, confirm)


def _required_secrets_key(environment: Environment, confirm: bool) -> str:
    secrets_key = _secret_from_environment_or_prompt(
        environment, EnvVariable.SECRETS_KEY, "Secrets key", confirm
    )
    if not secrets_key:
        raise ConfigurationError(f"A secrets key is required ({EnvVariable.SECRETS_KEY})")
    return secrets_key


def _encrypt_if_given(password: str, secrets_key: str, secret_name: str) -> str:
    return encrypt_secret(password, secrets_key, secret_name) if password else ""


# a password kept from the file must open with the same key, or the file would mix two keys
# and no single key could unlock it at install time
def _refuse_mixed_keys(
    config_path: Path, secrets: EncryptedSecretsConfig, secrets_key: str
) -> None:
    stored = config_file_setting_values(read_config_file(config_path))
    merged = {
        "secrets.luks_password_encrypted": secrets.luks_password_encrypted
        or stored.get("secrets.luks_password_encrypted"),
        "secrets.user_password_encrypted": secrets.user_password_encrypted
        or stored.get("secrets.user_password_encrypted"),
    }
    try:
        decrypted_credentials(merged, secrets_key)
    except ConfigurationError as error:
        raise ConfigurationError(
            "A password kept from the config file does not open with this key (another key, "
            "or the old format); enter both passwords to encrypt them with this key"
        ) from error


def _encrypt_secrets(environment: Environment) -> None:
    # a mistyped key would make the stored passwords unrecoverable, so it is asked twice
    secrets_key = _required_secrets_key(environment, confirm=True)
    luks_password = _secret_from_environment_or_prompt(
        environment, EnvVariable.LUKS_PASSWORD, "LUKS password (Enter keeps the stored one)", True
    )
    user_password = _secret_from_environment_or_prompt(
        environment, EnvVariable.USER_PASSWORD, "User password (Enter keeps the stored one)", True
    )
    if not (luks_password or user_password):
        raise ConfigurationError(
            f"No passwords provided ({EnvVariable.LUKS_PASSWORD} and/or {EnvVariable.USER_PASSWORD})"
        )
    secrets = EncryptedSecretsConfig(
        luks_password_encrypted=_encrypt_if_given(luks_password, secrets_key, LUKS_PASSWORD_SECRET),
        user_password_encrypted=_encrypt_if_given(user_password, secrets_key, USER_PASSWORD_SECRET),
    )
    print(f"luks_password_encrypted: {secrets.luks_password_encrypted or 'N/A'}")
    print(f"user_password_encrypted: {secrets.user_password_encrypted or 'N/A'}")

    if environment.switch_is_on(EnvVariable.NO_WRITE):
        logger.info("%s is set, the config file was not modified", EnvVariable.NO_WRITE)
        return
    config_path = _config_path(environment)
    _refuse_mixed_keys(config_path, secrets, secrets_key)
    write_encrypted_secrets(config_path, secrets)
    logger.info("Updated secrets in %s", config_path)


def _decrypt_secrets(environment: Environment) -> None:
    secrets_key = _required_secrets_key(environment, confirm=False)
    config_file_values = config_file_setting_values(read_config_file(_config_path(environment)))
    credentials = decrypted_credentials(config_file_values, secrets_key)
    print(f"LUKS: {credentials.get('credentials.luks_password', 'N/A')}")
    print(f"User: {credentials.get('credentials.user_password', 'N/A')}")


def main() -> int:
    return _run(_install, os.environ)


def clone_usb_boot() -> int:
    return _run(_clone_usb_boot, os.environ)


def usb_backup() -> int:
    return _run(_usb_backup, os.environ)


def encrypt_secrets() -> int:
    return _run(_encrypt_secrets, os.environ)


def decrypt_secrets() -> int:
    return _run(_decrypt_secrets, os.environ)


if __name__ == "__main__":
    sys.exit(main())
