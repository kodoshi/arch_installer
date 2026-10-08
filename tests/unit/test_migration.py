from dataclasses import replace

import pytest

from arch_installer.config.models import Credentials, MigrationConfig
from arch_installer.errors import MigrationError
from arch_installer.executors.migration import (
    STAGING_DIRECTORY,
    MigrationRestoreStepExecutor,
    MigrationStagingStepExecutor,
)
from tests.unit.conftest import build_config


def migration_config(**overrides) -> MigrationConfig:
    return replace(build_config().migration, enabled=True, **overrides)


@pytest.fixture
def existing_install_runner(fake_runner):
    # an old install on /dev/vda: vfat on vda1, LUKS on vda2, @home and sbctl keys inside
    fake_runner.set_response("lsblk -ln -o NAME /dev/vda", stdout="vda\nvda1\nvda2\n")
    fake_runner.set_response("cryptsetup isLuks /dev/vda1", exit_code=1)
    fake_runner.set_response("btrfs subvolume list", stdout="ID 257 gen 9 top level 5 path @home\n")
    fake_runner.set_response("du -sm", stdout="40\t/tmp/old-system/@home\n")
    fake_runner.set_response("df --output=avail", stdout=" Avail\n  8000\n")
    return fake_runner


def staging_config():
    return build_config(
        storage=replace(build_config().storage, target_disk="/dev/vda"),
        migration=migration_config(),
        credentials=Credentials(
            luks_password="new", user_password="user", source_luks_password="old-password"
        ),
    )


class TestMigrationStaging:
    def test_unlocks_the_source_with_its_password(self, existing_install_runner):
        MigrationStagingStepExecutor(staging_config(), existing_install_runner).execute()
        unlock = next(
            command
            for command in existing_install_runner.recorded_commands
            if "cryptsetup open" in command.command
        )
        assert "/dev/vda2" in unlock.command
        assert unlock.input_data == "old-password"

    def test_stages_home_and_secure_boot_keys(self, existing_install_runner):
        MigrationStagingStepExecutor(staging_config(), existing_install_runner).execute()
        existing_install_runner.assert_command_called(
            f"cp -a /tmp/old-system/@home/. {STAGING_DIRECTORY}/home/"
        )
        existing_install_runner.assert_command_called(
            f"cp -a /tmp/old-system/@/var/lib/sbctl/. {STAGING_DIRECTORY}/sbctl/"
        )

    def test_raises_when_no_luks_partition_found(self, fake_runner):
        fake_runner.set_response("lsblk -ln -o NAME /dev/vda", stdout="vda\nvda1\n")
        fake_runner.set_response("cryptsetup isLuks", exit_code=1)
        with pytest.raises(MigrationError, match="No LUKS partition"):
            MigrationStagingStepExecutor(staging_config(), fake_runner).execute()


class TestMigrationRestore:
    def test_restores_staged_directories(self, fake_runner):
        fake_runner.set_response(f"test -d {STAGING_DIRECTORY}", exit_code=0)
        fake_runner.set_response("test -d", exit_code=0)
        MigrationRestoreStepExecutor(staging_config(), fake_runner).execute()
        assert any("cp -a" in command for command in fake_runner.get_commands())

    def test_does_nothing_without_staging_data(self, fake_runner):
        fake_runner.set_response("test -d", exit_code=1)
        MigrationRestoreStepExecutor(staging_config(), fake_runner).execute()
        fake_runner.assert_command_not_called("cp -a")
