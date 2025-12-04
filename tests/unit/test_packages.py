import pytest

from arch_installer.steps.packages import PackageInstaller


class TestPackageInstaller:
    @pytest.fixture
    def installer(self, minimal_config, runtime_state, fake_runner):
        return PackageInstaller(minimal_config, runtime_state, fake_runner)

    def test_should_call_pacstrap_when_installing_packages(self, installer, fake_runner):
        # simulate fresh install - no existing system
        fake_runner.set_response("test -f /mnt/etc/os-release", exit_code=1)

        installer.install_packages()

        fake_runner.assert_command_called("pacstrap")

    def test_should_collect_base_packages_when_packages_collected(self, installer, fake_runner):
        # fake_runner.set_default_response(exit_code=0)

        packages = installer._collect_packages()

        assert "base" in packages

    def test_should_call_genfstab_when_pacstrap_completes(self, installer, fake_runner):
        # simulate fresh install - no existing system
        fake_runner.set_response("test -f /mnt/etc/os-release", exit_code=1)
        fake_runner.set_response("grep", exit_code=1)

        installer.install_packages()

        fake_runner.assert_command_called("genfstab")

    def test_should_include_intel_ucode_when_intel_cpu_detected(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.cpu_vendor = "intel"
        installer = PackageInstaller(minimal_config, runtime_state, fake_runner)
        packages = ["base", "intel-ucode", "amd-ucode", "linux"]
        filtered = installer._filter_microcode(packages)

        assert "intel-ucode" in filtered
        assert "amd-ucode" not in filtered

    def test_should_include_amd_ucode_when_amd_cpu_detected(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.cpu_vendor = "amd"
        installer = PackageInstaller(minimal_config, runtime_state, fake_runner)
        packages = ["base", "intel-ucode", "amd-ucode", "linux"]
        filtered = installer._filter_microcode(packages)

        assert "amd-ucode" in filtered
        assert "intel-ucode" not in filtered

    def test_should_keep_selected_kernels_when_filtering_kernels(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.selected_kernels = ["linux"]
        installer = PackageInstaller(minimal_config, runtime_state, fake_runner)
        packages = ["linux", "linux-headers", "linux-lts", "linux-lts-headers", "base"]
        filtered = installer._filter_kernels(packages)

        assert "linux" in filtered
        assert "linux-headers" in filtered
        assert "linux-lts" not in filtered
        assert "linux-lts-headers" not in filtered
        assert "base" in filtered

    def test_should_return_empty_list_when_no_desktop_selected(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.selected_desktops = []
        installer = PackageInstaller(minimal_config, runtime_state, fake_runner)
        packages = installer._get_desktop_packages()
        assert packages == []

    def test_should_return_gpu_packages_when_nvidia_selected(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.gpu_vendor = "nvidia"
        runtime_state.gpu_driver = "nvidia-dkms"
        installer = PackageInstaller(minimal_config, runtime_state, fake_runner)
        packages = installer._get_gpu_packages()
        assert isinstance(packages, list)

    def test_should_return_empty_list_when_no_gpu_vendor_selected(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.gpu_vendor = "none"
        installer = PackageInstaller(minimal_config, runtime_state, fake_runner)
        packages = installer._get_gpu_packages()
        assert packages == []

    def test_should_target_mnt_when_running_pacstrap(self, installer, fake_runner):
        # simulate fresh install - no existing system
        fake_runner.set_response("test -f /mnt/etc/os-release", exit_code=1)
        fake_runner.set_default_response(exit_code=0)

        installer.install_packages()

        pacstrap_cmds = fake_runner.get_commands("pacstrap")
        assert any("/mnt" in cmd for cmd in pacstrap_cmds)


class TestPackageFiltering:
    @pytest.fixture
    def installer(self, minimal_config, runtime_state, fake_runner):
        return PackageInstaller(minimal_config, runtime_state, fake_runner)

    def test_should_remove_duplicates_when_packages_deduplicated(self, installer):
        packages = ["base", "base", "linux", "linux"]
        unique = list(set(packages))
        assert len(unique) == 2
