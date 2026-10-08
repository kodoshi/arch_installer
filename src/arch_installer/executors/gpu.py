"""proprietary NVIDIA driver setup. AMD, Intel and nouveau need nothing beyond their packages.

the NVIDIA kernel modules for the initramfs are written by the UKI executor,
which owns mkinitcpio.conf.
"""

import logging

from arch_installer.executors.base import TARGET_ROOT, StepExecutor, write_file

logger = logging.getLogger(__name__)

NVIDIA_MODPROBE_DRM_OPTIONS = "options nvidia_drm modeset=1 fbdev=1"
NVIDIA_INITRAMFS_MODULES = ("nvidia", "nvidia_modeset", "nvidia_uvm", "nvidia_drm")
NVIDIA_DRIVER_PACKAGES = ("nvidia", "nvidia-dkms", "nvidia-open")


def initramfs_rebuild_hook(kernel_packages: tuple[str, ...]) -> str:
    targets = "\n".join(
        f"Target={package}" for package in (*NVIDIA_DRIVER_PACKAGES, *kernel_packages)
    )
    return f"""[Trigger]
Operation=Install
Operation=Upgrade
Operation=Remove
Type=Package
{targets}

[Action]
Description=Rebuilding initramfs after NVIDIA driver update...
Depends=mkinitcpio
When=PostTransaction
NeedsTargets
Exec=/bin/sh -c 'while read -r trg; do case $trg in linux*) exit 0; esac; done; /usr/bin/mkinitcpio -P'
"""


class GpuDriverStepExecutor(StepExecutor):
    def execute(self) -> None:
        logger.info("Configuring the NVIDIA %s driver...", self._config.gpu.driver or "dkms")

        self._runner.run(f"mkdir -p {TARGET_ROOT}/etc/modprobe.d")
        write_file(
            self._runner,
            f"{TARGET_ROOT}/etc/modprobe.d/nvidia.conf",
            f"{NVIDIA_MODPROBE_DRM_OPTIONS}\n",
        )

        self._runner.run(f"mkdir -p {TARGET_ROOT}/etc/pacman.d/hooks")
        write_file(
            self._runner,
            f"{TARGET_ROOT}/etc/pacman.d/hooks/nvidia.hook",
            initramfs_rebuild_hook(self._config.boot.kernel_packages),
        )
