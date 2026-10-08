"""config.yaml loading: YAML mapping -> InstallerConfig.

the loader is generic: it walks the dataclass type hints in models.py, so a key
is accepted only if the model declares it, and any key left out keeps the
model's default (for a nested section, the default of that section). typos and
wrong types fail loudly with the full key path.
"""

import types
from dataclasses import MISSING, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

import yaml

from arch_installer.config.models import InstallerConfig
from arch_installer.errors import ConfigurationError

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "config.yaml"


def read_config_file(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")
    try:
        with open(path) as config_file:
            raw = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Failed to parse configuration {path}: {error}") from error
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Configuration {path} must be a YAML mapping")
    return raw


def parse_config(raw: dict[str, Any]) -> InstallerConfig:
    return _build_dataclass(InstallerConfig, raw, path="", defaults=None)


def load_config(config_path: str | Path | None = None) -> InstallerConfig:
    return parse_config(read_config_file(config_path))


def _build_dataclass(cls: type, raw: Any, path: str, defaults: Any) -> Any:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path or 'configuration'} must be a mapping")

    declared = {field.name: field for field in fields(cls)}
    unknown = sorted(set(raw) - set(declared))
    if unknown:
        location = f" in {path}" if path else ""
        raise ConfigurationError(f"Unknown configuration key(s){location}: {', '.join(unknown)}")

    type_hints = get_type_hints(cls)
    values = {}
    for name, field in declared.items():
        key_path = f"{path}.{name}" if path else name
        if name in raw:
            values[name] = _convert(type_hints[name], raw[name], key_path, field.default)
        elif defaults is None and field.default is MISSING:
            raise ConfigurationError(f"Missing required configuration: {key_path}")
    if defaults is not None:
        return replace(defaults, **values)
    return cls(**values)


def _convert(expected_type: Any, value: Any, path: str, default: Any = MISSING) -> Any:
    origin = get_origin(expected_type)

    if origin in (Union, types.UnionType):
        if value is None:
            return None
        inner_type = next(arg for arg in get_args(expected_type) if arg is not type(None))
        return _convert(inner_type, value, path, default)

    if origin is tuple:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ConfigurationError(f"{path} must be a list")
        item_type = get_args(expected_type)[0]
        return tuple(_convert(item_type, item, f"{path}[{i}]") for i, item in enumerate(value))

    if is_dataclass(expected_type):
        # a section declared in YAML only changes the keys it lists
        section_defaults = default if isinstance(default, expected_type) else None
        return _build_dataclass(expected_type, value, path, section_defaults)

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
        if value is None:
            return ""
        if not isinstance(value, str):
            # e.g. an unquoted `on` that YAML turned into a boolean
            raise ConfigurationError(f"{path} must be a string, got {value!r} (quote it)")
    return value
