"""base system, kernels, microcode, desktops and GPU packages via pacstrap, plus fstab."""

import logging

from arch_installer.config.models import CpuVendor, GpuDriver, GpuVendor
from arch_installer.executors.base import TARGET_ROOT, StepExecutor, file_exists, write_file

logger = logging.getLogger(__name__)

MICROCODE_PACKAGES = {CpuVendor.AMD: "amd-ucode", CpuVendor.INTEL: "intel-ucode"}


class PackagesStepExecutor(StepExecutor):
    def execute(self) -> None:
        packages = self._collect_packages()
        logger.info("Installing %s packages...", len(packages))

        self._create_vconsole_config()
        self._clear_pacman_locks()
        self._install(packages)
        self._copy_pacman_config()
        self._generate_fstab()
        self._enable_services()

    def _collect_packages(self) -> list[str]:
        packages_config = self._config.packages
        packages = [
            *packages_config.base,
            *packages_config.cataloged,
        ]
        packages = self._without_other_microcode(packages)
        packages = self._without_unselected_kernels(packages)
        packages.extend(self._desktop_packages())
        packages.extend(self._gpu_packages())
        # keep the first occurrence, pacman rejects duplicate targets in some versions
        return list(dict.fromkeys(packages))

    def _without_other_microcode(self, packages: list[str]) -> list[str]:
        cpu_vendor = self._config.system.cpu_vendor
        if cpu_vendor == CpuVendor.UNKNOWN:
            return packages
        unwanted = {
            package for vendor, package in MICROCODE_PACKAGES.items() if vendor != cpu_vendor
        }
        return [package for package in packages if package not in unwanted]

    def _without_unselected_kernels(self, packages: list[str]) -> list[str]:
        boot = self._config.boot
        unselected = {kernel.package for kernel in boot.kernels} - set(boot.kernel_packages)
        unwanted = unselected | {f"{kernel}-headers" for kernel in unselected}
        return [package for package in packages if package not in unwanted]

    def _desktop_packages(self) -> list[str]:
        packages_config = self._config.packages
        desktops = packages_config.selected_desktops
        if not desktops:
            return []
        logger.info("Desktops: %s", ", ".join(desktops))
        packages = [
            package
            for desktop in desktops
            for package in packages_config.desktops.packages_for(desktop)
        ]
        return [*packages, *packages_config.display_manager]

    def _gpu_packages(self) -> list[str]:
        gpu = self._config.gpu
        drivers = gpu.drivers
        if gpu.vendor == GpuVendor.AMD:
            return list(drivers.amd)
        if gpu.vendor == GpuVendor.INTEL:
            return list(drivers.intel)
        if gpu.vendor == GpuVendor.NVIDIA:
            if gpu.driver == GpuDriver.NOUVEAU:
                return list(drivers.nouveau)
            if gpu.driver == GpuDriver.NVIDIA_OPEN:
                return list(drivers.nvidia_open)
            return list(drivers.nvidia_dkms)
        return []

    def _create_vconsole_config(self) -> None:
        # mkinitcpio's sd-vconsole hook runs during pacstrap and needs this file
        vconsole_path = f"{TARGET_ROOT}/etc/vconsole.conf"
        self._runner.run(f"mkdir -p {TARGET_ROOT}/etc")
        if not file_exists(self._runner, vconsole_path):
            write_file(self._runner, vconsole_path, f"KEYMAP={self._config.system.locale.keymap}\n")

    def _clear_pacman_locks(self) -> None:
        self._runner.run("rm -f /var/lib/pacman/db.lck", raise_on_nonzero_exit=False)
        self._runner.run(f"mkdir -p {TARGET_ROOT}/var/lib/pacman", raise_on_nonzero_exit=False)
        self._runner.run(f"rm -f {TARGET_ROOT}/var/lib/pacman/db.lck", raise_on_nonzero_exit=False)

    def _install(self, packages: list[str]) -> None:
        package_list = " ".join(packages)
        # an earlier run already bootstrapped the system: converge with pacman instead
        if file_exists(self._runner, f"{TARGET_ROOT}/etc/os-release"):
            logger.info("System already installed, updating packages instead of pacstrap...")
            self._runner.run_as_chroot(
                f"pacman -Syu --noconfirm --needed {package_list}", raise_on_nonzero_exit=False
            )
            return
        self._runner.run(f"pacstrap -K {TARGET_ROOT} --noconfirm {package_list}")

    def _copy_pacman_config(self) -> None:
        pacman_conf = f"{TARGET_ROOT}/etc/pacman.conf"
        if not file_exists(self._runner, pacman_conf):
            self._runner.run(f"cp /etc/pacman.conf {pacman_conf}")

    def _generate_fstab(self) -> None:
        fstab_path = f"{TARGET_ROOT}/etc/fstab"
        if self._runner.run(f"grep -q '^[^#]' {fstab_path}", raise_on_nonzero_exit=False).success:
            logger.info("fstab already has entries.")
            return
        logger.info("Generating fstab...")
        self._runner.run(f"genfstab -U {TARGET_ROOT} >> {fstab_path}")

    def _enable_services(self) -> None:
        self._runner.run_as_chroot("systemctl enable NetworkManager.service")

        if self._runner.run_as_chroot("pacman -Q openssh", raise_on_nonzero_exit=False).success:
            self._runner.run_as_chroot("systemctl enable sshd.service")

        if self._config.packages.selected_desktops:
            for display_manager in self._config.packages.display_manager:
                if self._runner.run_as_chroot(
                    f"pacman -Q {display_manager}", raise_on_nonzero_exit=False
                ).success:
                    self._runner.run_as_chroot(
                        f"systemctl enable {display_manager}.service", raise_on_nonzero_exit=False
                    )
