"""Runs the enabled install steps in order, then writes the files the installed system
keeps.
"""

import logging

import yaml

from arch_installer.config.models import InstallerConfig, exportable_config
from arch_installer.core import log
from arch_installer.core.command import CommandRunner
from arch_installer.executors.base import TARGET_ROOT, file_exists, write_file
from arch_installer.expected_state import EXPECTED_STATE_PATH, expected_state_file
from arch_installer.install_steps.registry import INSTALL_STEPS, InstallStep
from arch_installer.install_steps.wiring import config_lookup

logger = logging.getLogger(__name__)

UTILITY_SCRIPTS = (
    ("scripts/verify_install.sh", "verify-install"),
    ("scripts/manage_snapshot_entries.sh", "manage-snapshot-ukis"),
    ("scripts/dotfiles-sync.sh", "dotfiles-sync"),
)


class Installer:
    def __init__(self, config: InstallerConfig, runner: CommandRunner) -> None:
        self._config = config
        self._runner = runner

    def install(self) -> None:
        logger.info(log.banner("Declarative ArchLinux Installer (DALI)", "Starting installation"))
        self._log_summary()

        steps = self._enabled_steps()
        for index, step in enumerate(steps, 1):
            logger.info("[%s/%s] %s", index, len(steps), step)
            INSTALL_STEPS[step].executor(self._config, self._runner).execute()

        self._install_utility_scripts()
        self._write_expected_state()
        self._write_final_config()
        logger.info(log.banner("Installation complete"))

    def _enabled_steps(self) -> list[InstallStep]:
        value = config_lookup(self._config)
        return [step for step, wiring in INSTALL_STEPS.items() if wiring.enabled(value)]

    def _log_summary(self) -> None:
        system = self._config.system
        logger.info(
            "Host: %s, user: %s, disk: %s",
            system.hostname,
            system.user.name,
            self._config.storage.target_disk,
        )
        logger.info("Steps: %s", ", ".join(self._enabled_steps()))

    def _install_utility_scripts(self) -> None:
        scripts_directory = f"{TARGET_ROOT}/usr/local/bin"
        self._runner.run(f"mkdir -p {scripts_directory}")
        for source, name in UTILITY_SCRIPTS:
            if file_exists(self._runner, source):
                self._runner.run(f"install -m 755 {source} {scripts_directory}/{name}")

    def _write_expected_state(self) -> None:
        target_path = f"{TARGET_ROOT}{EXPECTED_STATE_PATH}"
        self._runner.run(f"install -d -m 755 {target_path.rsplit('/', 1)[0]}")
        write_file(self._runner, target_path, expected_state_file(self._config))
        # verify-install runs as root and sources this file, so only root may change it
        self._runner.run(f"chmod 644 {target_path}")
        logger.info("Wrote %s", EXPECTED_STATE_PATH)

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
