from dataclasses import replace

from arch_installer.config.models import GpuDriver, GpuVendor
from arch_installer.executors.base import Executor
from arch_installer.executors.docker import DockerExecutor
from arch_installer.executors.firewall import FirewallExecutor
from arch_installer.executors.gpu import NvidiaDriverExecutor
from arch_installer.executors.migration import MigrationStagingExecutor
from arch_installer.executors.storage import StorageExecutor
from arch_installer.executors.usb_boot import UsbBootExecutor
from arch_installer.installer import PIPELINE, Installer, Section
from tests.unit.conftest import build_config


def enabled_executors(config) -> set[type]:
    return {section.executor for section in PIPELINE if section.enabled(config)}


class TestPipelineSelection:
    def test_core_sections_always_run(self):
        executors = enabled_executors(build_config())
        assert StorageExecutor in executors

    def test_optional_sections_follow_their_flags(self):
        base = build_config()
        config = build_config(
            docker=replace(base.docker, enabled=False),
            firewall=replace(base.firewall, enabled=False),
            migration=replace(base.migration, enabled=False),
            usb_boot=replace(base.usb_boot, enabled=False),
        )
        executors = enabled_executors(config)
        assert DockerExecutor not in executors
        assert FirewallExecutor not in executors
        assert MigrationStagingExecutor not in executors
        assert UsbBootExecutor not in executors

    def test_enabling_flags_adds_their_executors(self):
        base = build_config()
        config = build_config(
            docker=replace(base.docker, enabled=True),
            migration=replace(base.migration, enabled=True),
            usb_boot=replace(base.usb_boot, enabled=True, device="/dev/sdb"),
        )
        executors = enabled_executors(config)
        assert DockerExecutor in executors
        assert MigrationStagingExecutor in executors
        assert UsbBootExecutor in executors

    def test_nvidia_driver_runs_only_for_a_proprietary_driver(self):
        nouveau = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NOUVEAU)
        )
        proprietary = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NVIDIA_DKMS)
        )
        assert NvidiaDriverExecutor not in enabled_executors(nouveau)
        assert NvidiaDriverExecutor in enabled_executors(proprietary)


class TestInstallerRun:
    def test_runs_only_enabled_sections_in_order_and_writes_final_config(
        self, fake_runner, monkeypatch
    ):
        calls: list[str] = []

        def section(label, enabled):
            executor = type(
                f"Recording{label}",
                (Executor,),
                {"execute": lambda self, label=label: calls.append(label)},
            )
            return Section(label, enabled, executor)

        pipeline = (
            section("always", lambda config: True),
            section("off", lambda config: False),
            section("on", lambda config: config.docker.enabled),
        )
        monkeypatch.setattr("arch_installer.installer.PIPELINE", pipeline)

        config = build_config(docker=replace(build_config().docker, enabled=True))
        Installer(config, fake_runner).install()

        assert calls == ["always", "on"]
        fake_runner.written_content("/mnt/home/testuser/final_config.yaml")
