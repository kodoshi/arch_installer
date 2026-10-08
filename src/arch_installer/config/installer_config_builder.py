"""setting values -> InstallerConfig.

a setting is addressed by its dotted path in the model ("storage.swap.size_mb"). the
builder walks the model's type hints: a path the model does not declare is refused, a
value of the wrong type is refused with its path, and every field must have a value, so
settings that no source provided are all reported together instead of being filled in.
"""

import types
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints

from arch_installer.config.models import InstallerConfig
from arch_installer.errors import ConfigurationError

# passwords are not required here: validate_for_install knows which ones an installation
# needs (the source password only for a migration), so an unprovided one stays empty
CREDENTIAL_PATHS = (
    "credentials.luks_password",
    "credentials.user_password",
    "credentials.source_luks_password",
)


class MissingSettingsError(ConfigurationError):
    def __init__(self, setting_paths: list[str]) -> None:
        self.setting_paths = setting_paths
        super().__init__("Missing settings: " + ", ".join(setting_paths))


def _nested(values: Mapping[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for setting_path, value in values.items():
        *sections, name = setting_path.split(".")
        section = nested
        for section_name in sections:
            section = section.setdefault(section_name, {})
            if not isinstance(section, dict):
                raise ConfigurationError(f"{setting_path} is inside a setting that is a value")
        section[name] = value
    return nested


def build_installer_config(values: Mapping[str, Any]) -> InstallerConfig:
    with_credentials = {**dict.fromkeys(CREDENTIAL_PATHS, ""), **values}
    missing: list[str] = []
    config = _build_section(InstallerConfig, _nested(with_credentials), "", missing)
    if missing:
        raise MissingSettingsError(missing)
    return config


def _build_section(section_type: type, raw: Any, path: str, missing: list[str]) -> Any:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path or 'configuration'} must be a mapping")

    declared = [field.name for field in fields(section_type)]
    unknown = sorted(set(raw) - set(declared))
    if unknown:
        location = f" in {path}" if path else ""
        raise ConfigurationError(f"Unknown configuration key(s){location}: {', '.join(unknown)}")

    type_hints = get_type_hints(section_type)
    arguments = {}
    for name in declared:
        setting_path = f"{path}.{name}" if path else name
        if name not in raw:
            missing.append(setting_path)
            continue
        arguments[name] = _convert(type_hints[name], raw[name], setting_path, missing)
    if len(arguments) < len(declared):
        return None
    return section_type(**arguments)


def _convert(expected_type: Any, value: Any, path: str, missing: list[str]) -> Any:
    origin = get_origin(expected_type)

    if origin in (Union, types.UnionType):
        if value is None:
            return None
        inner_type = next(arg for arg in get_args(expected_type) if arg is not type(None))
        return _convert(inner_type, value, path, missing)

    if origin is tuple:
        # a key written without a value in YAML (`mirrors:`) is an explicit empty list
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ConfigurationError(f"{path} must be a list")
        item_type = get_args(expected_type)[0]
        return tuple(
            _convert(item_type, item, f"{path}[{index}]", missing)
            for index, item in enumerate(value)
        )

    if is_dataclass(expected_type):
        return _build_section(expected_type, value, path, missing)

    if isinstance(expected_type, type) and issubclass(expected_type, Enum):
        try:
            return expected_type(value if value is not None else "")
        except ValueError:
            allowed = ", ".join(repr(member.value) for member in expected_type)
            raise ConfigurationError(f"{path}: {value!r} is not one of {allowed}") from None

    # bool is a subclass of int in Python, so YAML `true` must not pass as a number
    if expected_type is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ConfigurationError(f"{path} must be an integer, got {value!r}")
    if expected_type is bool and not isinstance(value, bool):
        raise ConfigurationError(f"{path} must be true or false, got {value!r}")
    if expected_type is str:
        # a key written without a value in YAML is an explicit empty string
        if value is None:
            return ""
        if not isinstance(value, str):
            # e.g. an unquoted `on` that YAML turned into a boolean
            raise ConfigurationError(f"{path} must be a string, got {value!r} (quote it)")
    return value
