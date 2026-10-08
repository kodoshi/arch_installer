"""Docker daemon configuration and the group allowed to use Docker."""

import json
import logging

from arch_installer.executors.base import TARGET_ROOT, StepExecutor, write_file
from arch_installer.executors.system import user_groups

logger = logging.getLogger(__name__)

DOCKER_GROUP = "docker"


def daemon_json(storage_driver: str, data_root: str) -> str:
    return json.dumps({"storage-driver": storage_driver, "data-root": data_root}, indent=4) + "\n"


def access_group_sudoers(access_group: str) -> str:
    return (
        f"# allow {access_group} group to run docker without password\n"
        f"%{access_group} ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose\n"
    )


class DockerStepExecutor(StepExecutor):
    def execute(self) -> None:
        docker = self._config.docker
        if not self._runner.run_as_chroot("pacman -Q docker", raise_on_nonzero_exit=False).success:
            logger.warning("docker is enabled but the docker package is not installed, skipping")
            return

        logger.info("Configuring docker (%s on %s)...", docker.storage_driver, docker.data_root)
        self._runner.run(f"mkdir -p {TARGET_ROOT}/etc/docker")
        write_file(
            self._runner,
            f"{TARGET_ROOT}/etc/docker/daemon.json",
            daemon_json(docker.storage_driver, docker.data_root),
        )
        self._runner.run_as_chroot("systemctl enable docker.service", raise_on_nonzero_exit=False)

        if not self._runner.run_as_chroot(
            f"getent group {docker.access_group}", raise_on_nonzero_exit=False
        ).success:
            self._runner.run_as_chroot(f"groupadd {docker.access_group}")

        username = self._config.system.user.name
        missing_groups = {DOCKER_GROUP, docker.access_group} - user_groups(self._runner, username)
        for group in sorted(missing_groups):
            logger.info("Adding %s to the %s group...", username, group)
            self._runner.run_as_chroot(f"usermod -aG {group} {username}")

        sudoers_file = f"{TARGET_ROOT}/etc/sudoers.d/{docker.access_group}"
        write_file(self._runner, sudoers_file, access_group_sudoers(docker.access_group))
        self._runner.run(f"chmod 440 {sudoers_file}")
