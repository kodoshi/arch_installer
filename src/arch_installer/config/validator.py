"""Configuration validator for config.yaml.

validates that the config file has required sections and values
based on installation mode (interactive vs non-interactive).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from arch_installer.errors import ConfigurationError


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]


# required sections that must always be present (have defaults but structure must be valid)
ALWAYS_REQUIRED_SECTIONS = frozenset(
    {
        "system",
        "storage",
        "boot",
        "packages",
    }
)

# sections that are required for non-interactive mode (no prompts available)
NON_INTERACTIVE_REQUIRED_SECTIONS = frozenset(
    {
        "system",
        "storage",
        "boot",
        "packages",
        "gpu",
    }
)

# optional sections that can be omitted entirely (feature disabled if missing)
OPTIONAL_SECTIONS = frozenset(
    {
        "docker",
        "dotfiles",
        "migration",
        "snapper",
        "firewall",
    }
)

# required fields within each section for non-interactive mode
NON_INTERACTIVE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "system": ["hostname", "timezone"],
    "storage": ["luks", "btrfs"],
    "boot": ["kernels", "hooks"],
    "packages": ["base"],
}


def validate_config_file(
    config_path: str | Path,
    non_interactive: bool = False,
) -> ValidationResult:
    """validate a config.yaml file for required structure.

    args:
        config_path: path to the config.yaml file
        non_interactive: if True, validates for non-interactive mode
                        (all required fields must be present)

    returns:
        ValidationResult with valid flag, errors, and warnings
    """
    config_path = Path(config_path)
    errors: list[str] = []
    warnings: list[str] = []

    if not config_path.exists():
        return ValidationResult(
            valid=False,
            errors=[f"Configuration file not found: {config_path}"],
            warnings=[],
        )

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return ValidationResult(
            valid=False,
            errors=[f"YAML parsing error: {e}"],
            warnings=[],
        )

    if raw is None:
        raw = {}

    _validate_required_sections(raw, non_interactive, errors, warnings)
    _validate_section_structure(raw, non_interactive, errors, warnings)
    _validate_optional_sections(raw, warnings)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def _validate_required_sections(
    raw: dict[str, Any],
    non_interactive: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    """check that required sections are present."""
    required = NON_INTERACTIVE_REQUIRED_SECTIONS if non_interactive else ALWAYS_REQUIRED_SECTIONS

    for section in required:
        if section not in raw or raw[section] is None:
            if non_interactive:
                errors.append(f"Missing required section '{section}' for non-interactive mode")
            else:
                warnings.append(f"Section '{section}' not found, will use defaults or prompt")


def _validate_section_structure(
    raw: dict[str, Any],
    non_interactive: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    """validate internal structure of sections."""
    if not non_interactive:
        return

    for section, required_fields in NON_INTERACTIVE_REQUIRED_FIELDS.items():
        if section not in raw:
            continue

        section_data = raw[section]
        if not isinstance(section_data, dict):
            errors.append(f"Section '{section}' must be a mapping/dictionary")
            continue

        for field in required_fields:
            if field not in section_data or section_data[field] is None:
                errors.append(
                    f"Missing required field '{section}.{field}' for non-interactive mode"
                )

    _validate_runtime_for_non_interactive(raw, errors)


def _validate_runtime_for_non_interactive(
    raw: dict[str, Any],
    errors: list[str],
) -> None:
    """validate that non-interactive mode has necessary values."""
    storage = raw.get("storage", {})
    if storage is None:
        storage = {}

    secrets = raw.get("secrets", {})
    if secrets is None:
        secrets = {}

    has_target_disk = bool(storage.get("target_disk"))
    has_encrypted_luks = bool(secrets.get("luks_password_encrypted"))
    has_encrypted_user = bool(secrets.get("user_password_encrypted"))

    if not has_target_disk:
        errors.append("Non-interactive mode requires 'storage.target_disk' or TARGET_DISK env var")

    if not has_encrypted_luks and not has_encrypted_user:
        errors.append(
            "Non-interactive mode requires encrypted passwords in secrets section "
            "or LUKS_PASSWORD/USER_PASSWORD env vars"
        )


def _validate_optional_sections(
    raw: dict[str, Any],
    warnings: list[str],
) -> None:
    """check optional sections and warn if they're missing."""
    for section in OPTIONAL_SECTIONS:
        if section not in raw:
            warnings.append(f"Optional section '{section}' not configured, feature disabled")
        else:
            section_data = raw.get(section, {})
            if isinstance(section_data, dict) and not section_data.get("enabled", True):
                warnings.append(f"Section '{section}' is explicitly disabled")


def validate_config_or_raise(
    config_path: str | Path,
    non_interactive: bool = False,
) -> None:
    """validate config and raise ConfigurationError if invalid.

    args:
        config_path: path to the config.yaml file
        non_interactive: if True, validates for non-interactive mode
    """
    result = validate_config_file(config_path, non_interactive)

    if result.warnings:
        for warning in result.warnings:
            print(f"Warning: {warning}")

    if not result.valid:
        error_msg = "Configuration validation failed:\n" + "\n".join(
            f"  - {e}" for e in result.errors
        )
        raise ConfigurationError(error_msg)
