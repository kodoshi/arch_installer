from dataclasses import replace

import pytest

from arch_installer.config.models import BackupCategory
from arch_installer.core.command import CommandExecutionResult
from arch_installer.errors import ConfigurationError
from arch_installer.executors.usb_backup import BACKUP_MOUNT, UsbBackupStepExecutor, package_catalog
from tests.unit import FakeCommandRunner
from tests.unit.conftest import build_config


def usb_config(**overrides):
    return build_config(
        sync=replace(
            build_config().sync,
            backup_partition=overrides.get("partition", "/dev/sdc1"),
            backup_categories=overrides.get("categories", tuple(BackupCategory)),
        ),
    )


@pytest.fixture
def backup_runner() -> FakeCommandRunner:
    runner = FakeCommandRunner()
    # heredoc writes always succeed regardless of the yaml payload they carry
    runner.set_handler("cat >", lambda command: CommandExecutionResult(command, 0, "", ""))
    runner.set_response("test -e /dev/sdc1", exit_code=0)
    runner.set_response("mountpoint", exit_code=1)
    runner.set_response("pacman -Qqe", stdout="base\nlinux\nvim\n")
    runner.set_response("date -Iseconds", stdout="2026-01-01T00:00:00+00:00")
    runner.set_response("hostname", stdout="testhost")
    return runner


class TestPackageCatalog:
    def test_renders_the_cataloged_yaml_shape(self):
        catalog = package_catalog(["base", "linux"])
        assert "cataloged:" in catalog
        assert "- base" in catalog and "- linux" in catalog


class TestUsbBackupExecutor:
    def test_mounts_and_unmounts_the_backup_partition(self, backup_runner):
        UsbBackupStepExecutor(
            usb_config(categories=(BackupCategory.DOTFILES,)), backup_runner
        ).execute()
        backup_runner.assert_command_called("mount /dev/sdc1")
        backup_runner.assert_command_called(f"umount {BACKUP_MOUNT}")

    def test_creates_a_directory_per_selected_category(self, backup_runner):
        categories = (BackupCategory.DOTFILES, BackupCategory.SYSTEM)
        UsbBackupStepExecutor(usb_config(categories=categories), backup_runner).execute()
        backup_runner.assert_command_called(f"mkdir -p {BACKUP_MOUNT}/dotfiles")
        backup_runner.assert_command_called(f"mkdir -p {BACKUP_MOUNT}/system")

    def test_writes_manifest_catalog_and_config(self, backup_runner):
        UsbBackupStepExecutor(
            usb_config(categories=(BackupCategory.DOTFILES,)), backup_runner
        ).execute()
        backup_runner.written_content(f"{BACKUP_MOUNT}/manifest.yaml")
        backup_runner.written_content(f"{BACKUP_MOUNT}/config/package_catalog.yaml")
        backup_runner.written_content(f"{BACKUP_MOUNT}/config/config.yaml")

    def test_exported_config_has_no_secrets(self, backup_runner):
        UsbBackupStepExecutor(
            usb_config(categories=(BackupCategory.DOTFILES,)), backup_runner
        ).execute()
        exported = backup_runner.written_content(f"{BACKUP_MOUNT}/config/config.yaml")
        assert "credentials" not in exported
        assert "secrets" not in exported

    def test_raises_when_the_partition_is_missing(self, backup_runner):
        backup_runner.set_response("test -e /dev/sdc1", exit_code=1)
        with pytest.raises(ConfigurationError, match="does not exist"):
            UsbBackupStepExecutor(usb_config(), backup_runner).execute()

    def test_raises_without_a_partition(self, backup_runner):
        with pytest.raises(ConfigurationError, match="No partition to back up to"):
            UsbBackupStepExecutor(usb_config(partition=""), backup_runner).execute()
