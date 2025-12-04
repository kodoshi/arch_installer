"""GPU driver setup."""

from arch_installer.core.command import CommandRunner
from arch_installer.templates.nvidia_gpu import (
    NVIDIA_MODPROBE_DRM_OPTIONS,
    NVIDIA_PACMAN_HOOK,
)


class GpuDriverSetup:
    """Configure GPU drivers.

    This step receives only what it needs:
    - runner: for executing commands
    - gpu_vendor: detected GPU vendor (amd, intel, nvidia, none)
    - gpu_driver: selected driver (for nvidia: nouveau, nvidia-open, nvidia-dkms)
    """

    def __init__(
        self,
        runner: CommandRunner,
        *,
        gpu_vendor: str = "",
        gpu_driver: str = "",
    ) -> None:
        self.runner = runner
        self.gpu_vendor = gpu_vendor
        self.gpu_driver = gpu_driver

    def configure_gpu(self) -> None:
        print(">>>>> Converging GPU configuration...")

        print(f"    GPU vendor: {self.gpu_vendor}")
        if self.gpu_driver:
            print(f"    GPU driver: {self.gpu_driver}")

        if self.gpu_vendor == "none" or not self.gpu_vendor:
            print("    No GPU-specific configuration needed.")
            return

        if self.gpu_vendor == "nvidia" and self.gpu_driver != "nouveau":
            self._configure_nvidia()
        elif self.gpu_vendor == "amd":
            print("    AMD GPU uses open-source drivers, no special configuration needed.")
        elif self.gpu_vendor == "intel":
            print("    Intel GPU uses open-source drivers, no special configuration needed.")

        print(">>>>> GPU configuration complete.")

    def _configure_nvidia(self) -> None:
        print("    Configuring NVIDIA settings...")

        # Add nvidia modules to initramfs
        self._add_nvidia_modules_to_initramfs()

        # Enable DRM kernel mode setting
        self._configure_nvidia_drm()

        # Install pacman hook for driver updates
        self._install_nvidia_pacman_hook()

    def _add_nvidia_modules_to_initramfs(self) -> None:
        mkinitcpio_conf = "/mnt/etc/mkinitcpio.conf"

        # Check if already configured
        result = self.runner.run(f"grep -q nvidia {mkinitcpio_conf}", raise_on_nonzero_exit=False)
        if result.success:
            return

        print("    Adding nvidia modules to initramfs...")
        self.runner.run(
            f"sed -i 's/^MODULES=(\\(.*\\))/MODULES=(\\1 nvidia nvidia_modeset nvidia_uvm nvidia_drm)/' {mkinitcpio_conf}"
        )

    def _configure_nvidia_drm(self) -> None:
        print("    Enabling NVIDIA DRM modeset...")

        modprobe_dir = "/mnt/etc/modprobe.d"
        self.runner.run(f"mkdir -p {modprobe_dir}")

        nvidia_conf = f"{modprobe_dir}/nvidia.conf"
        self.runner.run(f'echo "{NVIDIA_MODPROBE_DRM_OPTIONS}" > {nvidia_conf}')

    def _install_nvidia_pacman_hook(self) -> None:
        print("    Installing NVIDIA pacman hook...")

        hooks_dir = "/mnt/etc/pacman.d/hooks"
        self.runner.run(f"mkdir -p {hooks_dir}")

        self.runner.run(f"cat > {hooks_dir}/nvidia.hook << 'EOF'\n{NVIDIA_PACMAN_HOOK}EOF")
