import pytest

from arch_installer.steps.gpu import GpuDriverSetup


class TestGpuDriverSetup:
    @pytest.fixture
    def setup(self, fake_runner):
        return GpuDriverSetup(fake_runner)

    def test_should_enable_drm_modeset_when_nvidia_gpu_configured(self, fake_runner):
        # grep returns 1 = nvidia not yet in mkinitcpio.conf
        fake_runner.set_response("grep", exit_code=1)

        setup = GpuDriverSetup(fake_runner, gpu_vendor="nvidia", gpu_driver="nvidia-dkms")
        setup.configure_gpu()

        # should create modprobe config for nvidia drm
        fake_runner.assert_command_called("echo")
        fake_runner.assert_command_called("nvidia.conf")

    def test_should_add_nvidia_modules_when_nvidia_gpu_configured(self, fake_runner):
        # grep returns 1 = nvidia not yet in mkinitcpio.conf
        fake_runner.set_response("grep", exit_code=1)

        setup = GpuDriverSetup(fake_runner, gpu_vendor="nvidia", gpu_driver="nvidia-dkms")
        setup.configure_gpu()

        # modules should be added via sed
        fake_runner.assert_command_called("sed")
        fake_runner.assert_command_called("nvidia nvidia_modeset")

    def test_should_skip_adding_modules_when_already_configured(self, fake_runner):
        # grep returns 0 = nvidia already in mkinitcpio.conf
        fake_runner.set_response("grep", exit_code=0)

        setup = GpuDriverSetup(fake_runner, gpu_vendor="nvidia", gpu_driver="nvidia-dkms")
        setup.configure_gpu()

        # should NOT run sed since already configured
        fake_runner.assert_command_not_called("sed")

    def test_should_create_pacman_hook_when_nvidia_dkms_configured(self, fake_runner):
        fake_runner.set_response("grep", exit_code=1)

        setup = GpuDriverSetup(fake_runner, gpu_vendor="nvidia", gpu_driver="nvidia-dkms")
        setup._install_nvidia_pacman_hook()

        # should create hook directory and hook file
        fake_runner.assert_command_called("mkdir")
        fake_runner.assert_command_called("nvidia.hook")

    def test_should_require_minimal_config_when_amd_gpu_configured(self, fake_runner):
        setup = GpuDriverSetup(fake_runner, gpu_vendor="amd", gpu_driver="")
        setup.configure_gpu()

        # AMD typically needs less configuration - no nvidia-specific commands
        fake_runner.assert_command_not_called("nvidia")

    def test_should_require_minimal_config_when_intel_gpu_configured(self, fake_runner):
        setup = GpuDriverSetup(fake_runner, gpu_vendor="intel", gpu_driver="")
        setup.configure_gpu()

        # intel typically needs minimal configuration - no nvidia-specific commands
        fake_runner.assert_command_not_called("nvidia")

    def test_should_skip_config_when_gpu_disabled(self, fake_runner):
        setup = GpuDriverSetup(fake_runner, gpu_vendor="none")
        setup.configure_gpu()

        # should not run any GPU-specific commands
        assert len(fake_runner.recorded_commands) == 0

    def test_should_skip_nvidia_config_when_nouveau_driver_used(self, fake_runner):
        setup = GpuDriverSetup(fake_runner, gpu_vendor="nvidia", gpu_driver="nouveau")
        setup.configure_gpu()

        # nouveau is open source, shouldn't need proprietary nvidia config
        fake_runner.assert_command_not_called("nvidia_drm")
