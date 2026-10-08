from dataclasses import replace

from arch_installer.config.models import (
    CpuVendor,
    Desktop,
    GpuDriver,
    GpuVendor,
)
from arch_installer.executors.packages import PackagesStepExecutor
from tests.unit.conftest import build_config


def packages_executor(config, fake_runner):
    return PackagesStepExecutor(config, fake_runner)


class TestPackageCollection:
    def test_keeps_only_the_matching_microcode(self, fake_runner):
        config = build_config(
            system=replace(build_config().system, cpu_vendor=CpuVendor.INTEL),
            packages=replace(
                build_config().packages, base=("base", "intel-ucode", "amd-ucode", "linux")
            ),
        )
        collected = packages_executor(config, fake_runner)._collect_packages()
        assert "intel-ucode" in collected
        assert "amd-ucode" not in collected

    def test_drops_unselected_kernels_and_their_headers(self, fake_runner):
        base = build_config()
        config = build_config(
            packages=replace(
                build_config().packages, base=("base", "linux", "linux-lts", "linux-lts-headers")
            ),
            boot=replace(
                base.boot,
                kernels=(
                    base.boot.kernels[0],
                    replace(base.boot.kernels[0], name="lts", package="linux-lts"),
                ),
                selected_kernels=("linux",),
            ),
        )
        collected = packages_executor(config, fake_runner)._collect_packages()
        assert "linux" in collected
        assert "linux-lts" not in collected
        assert "linux-lts-headers" not in collected

    def test_includes_selected_desktop_and_display_manager_packages(self, fake_runner):
        config = build_config(
            packages=replace(
                build_config().packages,
                base=("base",),
                desktops=replace(build_config().packages.desktops, gnome=("gnome", "gdm")),
                selected_desktops=(Desktop.GNOME,),
                display_manager=("sddm",),
            )
        )
        collected = packages_executor(config, fake_runner)._collect_packages()
        assert "gnome" in collected and "sddm" in collected

    def test_includes_gpu_driver_packages_for_the_vendor(self, fake_runner):
        config = build_config(
            gpu=replace(
                build_config().gpu,
                vendor=GpuVendor.NVIDIA,
                driver=GpuDriver.NVIDIA_OPEN,
                drivers=replace(build_config().gpu.drivers, nvidia_open=("nvidia-open-dkms",)),
            )
        )
        collected = packages_executor(config, fake_runner)._collect_packages()
        assert "nvidia-open-dkms" in collected

    def test_appends_cataloged_packages_by_name(self, fake_runner):
        config = build_config(
            packages=replace(
                build_config().packages, base=("base",), cataloged=("neovim", "ripgrep")
            )
        )
        collected = packages_executor(config, fake_runner)._collect_packages()
        assert "neovim" in collected and "ripgrep" in collected


class TestPackageInstall:
    def test_pacstraps_a_fresh_system(self, fake_runner):
        fake_runner.set_response("test -f /mnt/etc/os-release", exit_code=1)
        fake_runner.set_response("grep", exit_code=1)
        packages_executor(build_config(), fake_runner).execute()
        fake_runner.assert_command_called("pacstrap")
        fake_runner.assert_command_called("genfstab")

    def test_converges_an_existing_system_with_pacman(self, fake_runner):
        fake_runner.set_response("test -f /mnt/etc/os-release", exit_code=0)
        fake_runner.set_response("grep", exit_code=0)
        packages_executor(build_config(), fake_runner).execute()
        fake_runner.assert_command_not_called("pacstrap")
        fake_runner.assert_command_called("pacman -Syu")
