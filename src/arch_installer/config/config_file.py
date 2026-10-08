"""config.yaml as a source of setting values, its encrypted passwords unlocked with the
secrets key. Keys the model does not declare are refused with their path.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml

from arch_installer.config.installer_config_builder import build_installer_config
from arch_installer.config.models import (
    LUKS_PASSWORD_SECRET,
    USER_PASSWORD_SECRET,
    InstallerConfig,
)
from arch_installer.core.secrets import decrypt_secret
from arch_installer.errors import ConfigurationError

# where config.yaml is read from when CONFIG_PATH is not set
REPOSITORY_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "config.yaml"

# encrypted setting path -> (credential it unlocks, name bound into its ciphertext)
ENCRYPTED_CREDENTIALS = {
    "secrets.luks_password_encrypted": ("credentials.luks_password", LUKS_PASSWORD_SECRET),
    "secrets.user_password_encrypted": ("credentials.user_password", USER_PASSWORD_SECRET),
}


def read_config_file(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigurationError(f"Configuration file not found: {config_path}")
    try:
        with open(config_path) as config_file:
            raw = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Failed to parse configuration {config_path}: {error}") from error
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Configuration {config_path} must be a YAML mapping")
    return raw


def config_file_setting_values(raw: Mapping[str, Any]) -> dict[str, Any]:
    return _flatten(InstallerConfig, raw, "")


def _flatten(section_type: type, raw: Any, prefix: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"{prefix.rstrip('.') or 'configuration'} must be a mapping")
    type_hints = get_type_hints(section_type)
    declared = {field.name for field in fields(section_type)}
    if section_type is InstallerConfig:
        declared.discard("credentials")
    unknown = sorted(set(raw) - declared)
    if unknown:
        location = f" in {prefix.rstrip('.')}" if prefix else ""
        raise ConfigurationError(f"Unknown configuration key(s){location}: {', '.join(unknown)}")

    values: dict[str, Any] = {}
    for name, value in raw.items():
        setting_path = f"{prefix}{name}"
        if is_dataclass(type_hints[name]) and isinstance(value, Mapping):
            values |= _flatten(type_hints[name], value, f"{setting_path}.")
        else:
            values[setting_path] = value
    return values


def has_encrypted_credentials(config_file_values: Mapping[str, Any]) -> bool:
    return any(config_file_values.get(encrypted_path) for encrypted_path in ENCRYPTED_CREDENTIALS)


def decrypted_credentials(
    config_file_values: Mapping[str, Any], secrets_key: str
) -> dict[str, str]:
    credentials = {}
    for encrypted_path, (credential_path, secret_name) in ENCRYPTED_CREDENTIALS.items():
        encrypted = config_file_values.get(encrypted_path)
        if encrypted:
            credentials[credential_path] = decrypt_secret(encrypted, secrets_key, secret_name)
    return credentials


# the file alone: no environment, no TUI, no decrypted passwords
def load_config_file(config_path: Path) -> InstallerConfig:
    return build_installer_config(config_file_setting_values(read_config_file(config_path)))
