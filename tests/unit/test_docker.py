from dataclasses import replace

from arch_installer.config.models import DockerConfig
from arch_installer.executors.docker import DockerExecutor, daemon_json
from tests.unit.conftest import build_config


def docker_config(**overrides) -> DockerConfig:
    return replace(DockerConfig(enabled=True), **overrides)


class TestDaemonJson:
    def test_sets_storage_driver_and_data_root(self):
        content = daemon_json("overlay2", "/var/lib/docker")
        assert '"storage-driver": "overlay2"' in content
        assert '"data-root": "/var/lib/docker"' in content


class TestDockerExecutor:
    def test_skips_when_docker_package_is_absent(self, fake_runner):
        fake_runner.set_response("pacman -Q docker", exit_code=1)
        DockerExecutor(build_config(docker=docker_config()), fake_runner).execute()
        fake_runner.assert_command_not_called("daemon.json")

    def test_configures_daemon_and_enables_service(self, fake_runner):
        fake_runner.set_response("pacman -Q docker", exit_code=0)
        fake_runner.set_response("getent group", exit_code=1)
        fake_runner.set_response("id -nG", stdout="testuser")
        DockerExecutor(build_config(docker=docker_config()), fake_runner).execute()
        fake_runner.written_content("/mnt/etc/docker/daemon.json")
        fake_runner.assert_command_called("systemctl enable docker.service")

    def test_adds_the_user_to_the_access_group(self, fake_runner):
        fake_runner.set_response("pacman -Q docker", exit_code=0)
        fake_runner.set_response("getent group", exit_code=1)
        fake_runner.set_response("id -nG", stdout="testuser")
        DockerExecutor(
            build_config(docker=docker_config(access_group="docker_access")), fake_runner
        ).execute()
        assert any(
            "usermod -aG docker_access testuser" in command
            for command in fake_runner.get_commands()
        )
