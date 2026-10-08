from dataclasses import replace

from arch_installer.config.models import GpuDriver, GpuVendor
from arch_installer.executors.gpu import GpuDriverStepExecutor, initramfs_rebuild_hook
from tests.unit.conftest import build_config


class TestProprietaryNvidiaGating:
    def test_nvidia_with_proprietary_driver_counts_as_proprietary(self):
        config = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NVIDIA_DKMS)
        )
        assert config.gpu.uses_proprietary_nvidia_driver

    def test_nvidia_with_nouveau_is_not_proprietary(self):
        config = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NOUVEAU)
        )
        assert not config.gpu.uses_proprietary_nvidia_driver

    def test_amd_is_not_proprietary(self):
        config = build_config(gpu=replace(build_config().gpu, vendor=GpuVendor.AMD))
        assert not config.gpu.uses_proprietary_nvidia_driver


class TestNvidiaDriverExecutor:
    def test_writes_drm_modeset_options(self, fake_runner):
        config = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NVIDIA_DKMS)
        )
        GpuDriverStepExecutor(config, fake_runner).execute()

        assert "modeset=1" in fake_runner.written_content("/mnt/etc/modprobe.d/nvidia.conf")

    def test_rebuild_hook_targets_the_configured_kernels(self, fake_runner):
        config = build_config(
            gpu=replace(build_config().gpu, vendor=GpuVendor.NVIDIA, driver=GpuDriver.NVIDIA_DKMS),
            boot=replace(build_config().boot, selected_kernels=("linux-lts",)),
        )
        GpuDriverStepExecutor(config, fake_runner).execute()

        hook = fake_runner.written_content("/mnt/etc/pacman.d/hooks/nvidia.hook")
        assert "Target=linux-lts" in hook
        assert "Target=nvidia-dkms" in hook


class TestInitramfsHook:
    def test_lists_driver_and_kernel_targets(self):
        hook = initramfs_rebuild_hook(("linux",))
        assert "Target=nvidia" in hook
        assert "Target=linux" in hook
