from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

from arch_installer.config.config_file import (
    config_file_setting_values,
    load_config_file,
    read_config_file,
)
from arch_installer.config.installer_config_builder import build_installer_config
from arch_installer.config.models import Credentials, InstallerConfig
from arch_installer.core import secrets
from arch_installer.core.secrets import Argon2Cost
from tests.unit import FakeCommandRunner

EXAMPLE_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"
DEMO_CONFIG_PATH = EXAMPLE_CONFIG_PATH.parent / "demo_config.yaml"
# every setting explicit, as the installer requires: the base the unit tests vary
UNIT_CONFIG_PATH = Path(__file__).parent.parent / "data" / "unit_config.yaml"
UNIT_CREDENTIALS = Credentials(
    luks_password="testpassword", user_password="userpassword", source_luks_password=""
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # executors poll for partitions with time.sleep; nothing to wait for with a fake runner
    monkeypatch.setattr("time.sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _cheap_secret_encryption(monkeypatch):
    # the real Argon2id cost takes about 0.3 s per secret; the format is the same either way
    monkeypatch.setattr(secrets, "NEW_SECRET_COST", Argon2Cost(memory_kib=8, iterations=1, lanes=1))


@pytest.fixture
def fake_runner() -> FakeCommandRunner:
    return FakeCommandRunner()


@pytest.fixture
def example_config() -> InstallerConfig:
    # the shipped config.yaml leaves the target disk to TARGET_DISK or the TUI
    raw = read_config_file(EXAMPLE_CONFIG_PATH)
    raw["storage"]["target_disk"] = "/dev/vda"
    return build_installer_config(config_file_setting_values(raw))


@cache
def _unit_config() -> InstallerConfig:
    return replace(load_config_file(UNIT_CONFIG_PATH), credentials=UNIT_CREDENTIALS)


def build_config(**overrides) -> InstallerConfig:
    return replace(_unit_config(), **overrides)


@pytest.fixture
def test_config() -> InstallerConfig:
    return build_config()
