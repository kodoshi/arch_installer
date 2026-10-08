from dataclasses import replace
from pathlib import Path

import pytest

from arch_installer.config.loader import load_config
from arch_installer.config.models import (
    BtrfsConfig,
    Credentials,
    InstallerConfig,
    PackagesConfig,
    StorageConfig,
    SubvolumeConfig,
    SwapConfig,
    SystemConfig,
    UserConfig,
)
from tests.unit import FakeCommandRunner

EXAMPLE_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # executors poll for partitions with time.sleep; nothing to wait for with a fake runner
    monkeypatch.setattr("time.sleep", lambda *_: None)


@pytest.fixture
def fake_runner() -> FakeCommandRunner:
    return FakeCommandRunner()


@pytest.fixture
def example_config() -> InstallerConfig:
    # the shipped config.yaml, so a drift between it and the model fails a test here
    return load_config(EXAMPLE_CONFIG_PATH)


def build_config(**overrides) -> InstallerConfig:
    base = InstallerConfig(
        system=SystemConfig(hostname="testhost", timezone="UTC", user=UserConfig(name="testuser")),
        packages=PackagesConfig(base=("base", "linux", "linux-firmware", "btrfs-progs")),
        storage=StorageConfig(
            target_disk="/dev/loop0",
            efi_size_mb=512,
            btrfs=BtrfsConfig(
                subvolumes=(SubvolumeConfig("@", "/"), SubvolumeConfig("@home", "/home"))
            ),
            swap=SwapConfig(enabled=True, size_mb=1024),
        ),
        credentials=Credentials(luks_password="testpassword", user_password="userpassword"),
    )
    return replace(base, **overrides) if overrides else base


@pytest.fixture
def test_config() -> InstallerConfig:
    return build_config()
