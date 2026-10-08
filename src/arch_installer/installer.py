"""the installation orchestrator.

it holds the ordered list of sections, asks the config which ones are enabled, and
runs each enabled section's executor. it decides nothing about how a section works;
that lives entirely in the executor.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

import yaml

from arch_installer.config.models import InstallerConfig, exportable_config
from arch_installer.core import log
from arch_installer.core.command import CommandRunner
from arch_installer.executors.base import TARGET_ROOT, Executor, file_exists, write_file
from arch_installer.executors.boot import BootloaderExecutor, UkiExecutor
from arch_installer.executors.docker import DockerExecutor
from arch_installer.executors.firewall import FirewallExecutor
from arch_installer.executors.gpu import NvidiaDriverExecutor
from arch_installer.executors.migration import MigrationRestoreExecutor, MigrationStagingExecutor
from arch_installer.executors.mirrors import MirrorsExecutor
from arch_installer.executors.packages import PackagesExecutor
from arch_installer.executors.snapper import (
    SnapperExecutor,
    SnapshotBootExecutor,
    SnapshotNotificationsExecutor,
)
from arch_installer.executors.storage import StorageExecutor
from arch_installer.executors.system import SystemExecutor
from arch_installer.executors.usb_boot import UsbBootExecutor

logger = logging.getLogger(__name__)

UTILITY_SCRIPTS = (
    ("scripts/verify_install.sh", "verify-install"),
    ("scripts/manage_snapshot_entries.sh", "manage-snapshot-ukis"),
    ("scripts/dotfiles-sync.sh", "dotfiles-sync"),
)


@dataclass(frozen=True)
class Section:
    label: str
    enabled: Callable[[InstallerConfig], bool]
    executor: type[Executor]


PIPELINE = (
    Section("Migration staging", lambda config: config.migration.enabled, MigrationStagingExecutor),
    Section("Storage", lambda config: True, StorageExecutor),
    Section("Pacman mirrors", lambda config: _mirrors_declared(config), MirrorsExecutor),
    Section("Packages", lambda config: True, PackagesExecutor),
    Section("Migration restore", lambda config: config.migration.enabled, MigrationRestoreExecutor),
    Section("System", lambda config: True, SystemExecutor),
    Section("Docker", lambda config: config.docker.enabled, DockerExecutor),
    Section(
        "GPU driver", lambda config: config.gpu.uses_proprietary_nvidia_driver, NvidiaDriverExecutor
    ),
    Section("Kernel images", lambda config: True, UkiExecutor),
    Section("Bootloader", lambda config: True, BootloaderExecutor),
    Section("USB boot drive", lambda config: config.usb_boot.enabled, UsbBootExecutor),
    Section("Snapper", lambda config: config.snapper.enabled, SnapperExecutor),
    Section(
        "Bootable snapshots", lambda config: config.boot.enable_snapshot_boot, SnapshotBootExecutor
    ),
    Section(
        "Snapshot notifications",
        lambda config: config.notifications.enabled and config.snapper.enabled,
        SnapshotNotificationsExecutor,
    ),
    Section("Firewall", lambda config: config.firewall.enabled, FirewallExecutor),
)


def _mirrors_declared(config: InstallerConfig) -> bool:
    mirrors = config.system.mirrors
    return mirrors.use_reflector or bool(mirrors.mirrors)


class Installer:
    def __init__(self, config: InstallerConfig, runner: CommandRunner) -> None:
        self._config = config
        self._runner = runner

    def install(self) -> None:
        logger.info(log.banner("Declarative ArchLinux Installer (DALI)", "Starting installation"))
        self._log_summary()

        enabled = [section for section in PIPELINE if section.enabled(self._config)]
        for index, section in enumerate(enabled, 1):
            logger.info("[%s/%s] %s", index, len(enabled), section.label)
            section.executor(self._config, self._runner).execute()

        self._install_utility_scripts()
        self._write_final_config()
        logger.info(log.banner("Installation complete"))

    def _log_summary(self) -> None:
        system = self._config.system
        features = [section.label for section in PIPELINE if section.enabled(self._config)]
        logger.info(
            "Host: %s, user: %s, disk: %s",
            system.hostname,
            system.user.name,
            self._config.storage.target_disk,
        )
        logger.info("Sections: %s", ", ".join(features))

    def _install_utility_scripts(self) -> None:
        scripts_dir = f"{TARGET_ROOT}/usr/local/bin"
        self._runner.run(f"mkdir -p {scripts_dir}")
        for source, name in UTILITY_SCRIPTS:
            if file_exists(self._runner, source):
                self._runner.run(f"install -m 755 {source} {scripts_dir}/{name}")

    def _write_final_config(self) -> None:
        username = self._config.system.user.name
        user_home = f"{TARGET_ROOT}/home/{username}"
        final_config = f"{user_home}/final_config.yaml"
        self._runner.run(f"mkdir -p {user_home}", raise_on_nonzero_exit=False)
        write_file(
            self._runner,
            final_config,
            yaml.safe_dump(
                exportable_config(self._config), default_flow_style=False, sort_keys=False
            ),
        )
        self._runner.run_as_chroot(
            f"chown {username}:{username} /home/{username}/final_config.yaml",
            raise_on_nonzero_exit=False,
        )
        logger.info("Wrote /home/%s/final_config.yaml", username)
