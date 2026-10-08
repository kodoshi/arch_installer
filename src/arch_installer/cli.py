"""command-line entry points: `arch-installer`, `usb-init`, `usb-backup`, and the
secrets helpers behind `make encrypt-secrets` / `make decrypt-secrets`.

each installer entry point resolves the configuration the same way (defaults,
config.yaml, encrypted secrets, environment variables, then the TUI when interactive)
and hands the finished InstallerConfig to an orchestrator or a single executor.
"""

import getpass
import logging
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from arch_installer.config.environment import Environment, EnvVar, unlock_secrets
from arch_installer.config.loader import DEFAULT_CONFIG_PATH, load_config
from arch_installer.config.models import EncryptedSecretsConfig, InstallerConfig
from arch_installer.config.secrets_file import write_encrypted_secrets
from arch_installer.core import log
from arch_installer.core.command import SystemCommandRunner
from arch_installer.core.secrets import encrypt_secret
from arch_installer.errors import ArchInstallerError, ConfigurationError
from arch_installer.executors.usb_backup import UsbBackupExecutor
from arch_installer.executors.usb_boot import UsbBootExecutor
from arch_installer.installer import Installer
from arch_installer.tui.app import run_tui_setup

logger = logging.getLogger(log.PACKAGE_LOGGER_NAME)


def _log_level(environment: Environment) -> int:
    verbose = environment.text(EnvVar.VERBOSE).lower()
    if verbose == "true":
        return logging.DEBUG
    if verbose == "quiet":
        return logging.WARNING
    return logging.INFO


def _is_interactive(environment: Environment) -> bool:
    return not environment.flag(EnvVar.NON_INTERACTIVE)


def resolve_config(
    environment: Environment,
    interactive: bool,
    tui: Callable[[InstallerConfig], InstallerConfig] | None = None,
) -> InstallerConfig:
    config = load_config(environment.text(EnvVar.CONFIG_PATH) or None)
    config = _unlock_if_needed(config, environment, interactive)
    config = environment.override(config)
    if interactive and tui is not None:
        config = tui(config)
    return config


def _unlock_if_needed(
    config: InstallerConfig, environment: Environment, interactive: bool
) -> InstallerConfig:
    both_from_env = environment.is_set(EnvVar.LUKS_PASSWORD) and environment.is_set(
        EnvVar.USER_PASSWORD
    )
    if not config.secrets.configured or both_from_env:
        return config

    secrets_key = environment.text(EnvVar.SECRETS_KEY)
    if not secrets_key and interactive:
        secrets_key = getpass.getpass("Secrets decryption key: ")
    if not secrets_key:
        return config
    return unlock_secrets(config, secrets_key)


def validate_for_install(config: InstallerConfig) -> None:
    problems = []
    if not config.credentials.luks_password:
        problems.append(f"no LUKS password ({EnvVar.LUKS_PASSWORD} or encrypted secrets)")
    if not config.credentials.user_password:
        problems.append(f"no user password ({EnvVar.USER_PASSWORD} or encrypted secrets)")
    if not config.storage.target_disk:
        problems.append(f"no target disk ({EnvVar.TARGET_DISK} or storage.target_disk)")
    if config.migration.enabled and not config.credentials.source_luks_password:
        problems.append(f"migration needs the old LUKS password ({EnvVar.SOURCE_LUKS_PASSWORD})")
    if config.usb_boot.enabled and not config.usb_boot.device:
        problems.append(f"USB boot needs a device ({EnvVar.USB_BOOT_DEVICE} or usb_boot.device)")
    if problems:
        raise ConfigurationError("Cannot start the installation:\n  - " + "\n  - ".join(problems))


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
    interactive = _is_interactive(environment)
    config = resolve_config(environment, interactive, tui=run_tui_setup)
    validate_for_install(config)
    Installer(config, SystemCommandRunner()).install()


def _usb_init(environment: Environment) -> None:
    config = resolve_config(environment, interactive=False)
    if not config.usb_boot.device:
        raise ConfigurationError(f"No USB device ({EnvVar.USB_BOOT_DEVICE} or usb_boot.device)")
    runner = SystemCommandRunner()
    UsbBootExecutor(config, runner).execute()
    UsbBackupExecutor(config, runner).execute()


def _usb_backup(environment: Environment) -> None:
    config = resolve_config(environment, interactive=False)
    UsbBackupExecutor(config, SystemCommandRunner()).execute()


def _required_secrets_key(environment: Environment) -> str:
    secrets_key = environment.text(EnvVar.SECRETS_KEY)
    if not secrets_key:
        raise ConfigurationError(f"{EnvVar.SECRETS_KEY} is required")
    return secrets_key


def _encrypt_if_given(password: str, secrets_key: str) -> str:
    return encrypt_secret(password, secrets_key) if password else ""


def _encrypt_secrets(environment: Environment) -> None:
    secrets_key = _required_secrets_key(environment)
    luks_password = environment.text(EnvVar.LUKS_PASSWORD)
    user_password = environment.text(EnvVar.USER_PASSWORD)
    if not (luks_password or user_password):
        raise ConfigurationError(
            f"No passwords provided ({EnvVar.LUKS_PASSWORD} and/or {EnvVar.USER_PASSWORD})"
        )
    secrets = EncryptedSecretsConfig(
        luks_password_encrypted=_encrypt_if_given(luks_password, secrets_key),
        user_password_encrypted=_encrypt_if_given(user_password, secrets_key),
    )
    print(f"luks_password_encrypted: {secrets.luks_password_encrypted or 'N/A'}")
    print(f"user_password_encrypted: {secrets.user_password_encrypted or 'N/A'}")

    if environment.flag(EnvVar.NO_WRITE):
        logger.info("%s is set, the config file was not modified", EnvVar.NO_WRITE)
        return
    config_path = Path(environment.text(EnvVar.CONFIG_PATH) or DEFAULT_CONFIG_PATH)
    write_encrypted_secrets(config_path, secrets)
    logger.info("Updated secrets in %s", config_path)


def _decrypt_secrets(environment: Environment) -> None:
    secrets_key = _required_secrets_key(environment)
    config = load_config(environment.text(EnvVar.CONFIG_PATH) or None)
    credentials = unlock_secrets(config, secrets_key).credentials
    print(f"LUKS: {credentials.luks_password or 'N/A'}")
    print(f"User: {credentials.user_password or 'N/A'}")


def main() -> int:
    return _run(_install, os.environ)


def usb_init() -> int:
    return _run(_usb_init, os.environ)


def usb_backup() -> int:
    return _run(_usb_backup, os.environ)


def encrypt_secrets() -> int:
    return _run(_encrypt_secrets, os.environ)


def decrypt_secrets() -> int:
    return _run(_decrypt_secrets, os.environ)


if __name__ == "__main__":
    sys.exit(main())
