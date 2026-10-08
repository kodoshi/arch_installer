from dataclasses import replace

from arch_installer.config.models import GpuDriver, GpuVendor
from arch_installer.executors.base import Executor
from arch_installer.executors.docker import DockerStepExecutor
from arch_installer.executors.firewall import FirewallStepExecutor
from arch_installer.executors.gpu import GpuDriverStepExecutor
from arch_installer.executors.migration import MigrationStagingStepExecutor
from arch_installer.executors.storage import StorageStepExecutor
from arch_installer.executors.usb_boot import UsbBootExecutor
from arch_installer.install_steps.registry import INSTALL_STEPS, InstallStep
from arch_installer.install_steps.wiring import StepWiring, always, config_lookup, when
from arch_installer.installer import Installer
from tests.unit.conftest import build_config


def enabled_executors(config) -> set[type]:
    value = config_lookup(config)
    return {wiring.executor for wiring in INSTALL_STEPS.values() if wiring.enabled(value)}


class TestStepSelection:
    def test_core_steps_always_run(self):
        executors = enabled_executors(build_config())
        assert StorageStepExecutor in executors

    def test_optional_steps_follow_their_switches(self):
        base = build_config()
        config = build_config(
            docker=replace(base.docker, enabled=False),
            firewall=replace(base.firewall, enabled=False),
            migration=replace(base.migration, enabled=False),
            usb_boot=replace(base.usb_boot, enabled=False),
        )
        executors = enabled_executors(config)
        assert DockerStepExecutor not in executors
        assert FirewallStepExecutor not in executors
        assert MigrationStagingStepExecutor not in executors
        assert UsbBootExecutor not in executors

    def test_switching_steps_on_adds_their_executors(self):
        base = build_config()
        config = build_config(
            docker=replace(base.docker, enabled=True),
            migration=replace(base.migration, enabled=True),
            usb_boot=replace(base.usb_boot, enabled=True, device="/dev/sdb"),
        )
        executors = enabled_executors(config)
        assert DockerStepExecutor in executors
        assert MigrationStagingStepExecutor in executors
        assert UsbBootExecutor in executors

    def test_nvidia_driver_runs_only_for_a_proprietary_driver(self):
        nouveau = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NOUVEAU)
        )
        proprietary = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NVIDIA_DKMS)
        )
        assert GpuDriverStepExecutor not in enabled_executors(nouveau)
        assert GpuDriverStepExecutor in enabled_executors(proprietary)


class TestInstallerRun:
    def test_runs_only_enabled_steps_in_registry_order_and_writes_final_config(
        self, fake_runner, monkeypatch
    ):
        calls: list[str] = []

        def recording_step(label: str, enabled) -> StepWiring:
            executor = type(
                f"Recording{label}",
                (Executor,),
                {"execute": lambda self, label=label: calls.append(label)},
            )
            return StepWiring(config_sections=(), settings=(), enabled=enabled, executor=executor)

        steps = {
            InstallStep.STORAGE: recording_step("storage", always),
            InstallStep.FIREWALL: recording_step("firewall", lambda value: False),
            InstallStep.DOCKER: recording_step("docker", when("docker.enabled")),
        }
        monkeypatch.setattr("arch_installer.installer.INSTALL_STEPS", steps)

        config = build_config(docker=replace(build_config().docker, enabled=True))
        Installer(config, fake_runner).install()

        assert calls == ["storage", "docker"]
        fake_runner.written_content("/mnt/home/testuser/final_config.yaml")
