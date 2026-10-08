"""stores encrypted passwords in the secrets section of a config.yaml.

the file is edited line by line rather than re-dumped, so its comments and layout
survive; the edited text is parsed before it is written, to prove the values land
where the loader looks for them.
"""

import re
from dataclasses import fields
from pathlib import Path

import yaml

from arch_installer.config.config_file import config_file_setting_values
from arch_installer.config.models import EncryptedSecretsConfig
from arch_installer.errors import ConfigurationError

SECRETS_SECTION_PATTERN = re.compile(r"^secrets:[ \t]*(#.*)?$", re.MULTILINE)


def _set_secret_line(config_text: str, key: str, encrypted_value: str) -> str:
    entry = f"{key}: '{encrypted_value}'"
    existing_entry = re.compile(rf"^(?P<indent>[ \t]+){key}:.*$", re.MULTILINE)
    if existing_entry.search(config_text):
        return existing_entry.sub(lambda match: match["indent"] + entry, config_text, count=1)
    if SECRETS_SECTION_PATTERN.search(config_text):
        return SECRETS_SECTION_PATTERN.sub(
            lambda match: f"{match[0]}\n  {entry}", config_text, count=1
        )
    return config_text.rstrip("\n") + f"\n\nsecrets:\n  {entry}\n"


def _verify_stored(config_text: str, secrets: EncryptedSecretsConfig, config_path: Path) -> None:
    try:
        stored = config_file_setting_values(yaml.safe_load(config_text) or {})
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Editing {config_path} would break its YAML: {error}") from error
    for secret_field in fields(secrets):
        expected = getattr(secrets, secret_field.name)
        if expected and stored.get(f"secrets.{secret_field.name}") != expected:
            raise ConfigurationError(
                f"Could not place {secret_field.name} under 'secrets' in {config_path}"
            )


# only the non-empty values are written; an empty one keeps what the file already has
def write_encrypted_secrets(config_path: Path, secrets: EncryptedSecretsConfig) -> None:
    if not config_path.exists():
        raise ConfigurationError(f"Configuration file not found: {config_path}")

    config_text = config_path.read_text()
    for secret_field in fields(secrets):
        encrypted_value = getattr(secrets, secret_field.name)
        if encrypted_value:
            config_text = _set_secret_line(config_text, secret_field.name, encrypted_value)

    _verify_stored(config_text, secrets, config_path)
    config_path.write_text(config_text)
