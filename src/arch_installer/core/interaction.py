"""
abstract interface for user interaction during installation.

this module defines the Strategy pattern for collecting user input,
with implementations for CLI prompts and GUI screens.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from arch_installer.core.command import CommandRunner, SystemCommandRunner


@dataclass
class DiskInfo:
    name: str
    path: str
    model: str
    size: str
    partitions: list[str]


@dataclass
class MenuOption:
    value: str
    description: str


@dataclass
class InstallationSelections:
    """holds all user selections collected during interactive setup."""

    target_disk: str = ""
    luks_password: str = ""
    user_password: str = ""
    source_luks_password: str = ""
    gpu_vendor: str = "none"
    gpu_driver: str = ""
    cpu_vendor: str = "amd"
    selected_desktops: list[str] | None = None
    swap_size_mb: int = 8192
    wipe_method: str = "quick"
    enable_hibernation: bool = True
    enable_firewall: bool = True
    enable_snapshot_boot: bool = False
    enable_docker: bool = False
    enable_migration: bool = False
    hostname: str = ""
    username: str = ""
    timezone: str = ""
    keymap: str = "us"
    locale: str = "en_US.UTF-8"

    def __post_init__(self):
        if self.selected_desktops is None:
            self.selected_desktops = []


# predefined options for selections
GPU_OPTIONS: list[MenuOption] = [
    MenuOption("amd", "AMD (AMDGPU, open-source)"),
    MenuOption("intel", "Intel (integrated graphics)"),
    MenuOption("nvidia", "NVIDIA (proprietary/nouveau)"),
    MenuOption("none", "None (VM or generic)"),
]

NVIDIA_DRIVER_OPTIONS: list[MenuOption] = [
    MenuOption("nouveau", "open-source, limited features"),
    MenuOption("nvidia-open", "official open kernel modules, RTX 20+"),
    MenuOption("nvidia-dkms", "proprietary, best compatibility"),
]

CPU_OPTIONS: list[MenuOption] = [
    MenuOption("intel", "Intel"),
    MenuOption("amd", "AMD"),
]

DESKTOP_OPTIONS: list[MenuOption] = [
    MenuOption("gnome", "GNOME (Wayland, modern, intuitive)"),
    MenuOption("kde", "KDE Plasma (Wayland, highly customizable)"),
    MenuOption("hyprland", "Hyprland (Wayland tiling WM, power users)"),
    MenuOption("all", "All three (choose at login via SDDM)"),
    MenuOption("none", "None (headless server / minimal)"),
]

SWAP_OPTIONS: list[MenuOption] = [
    MenuOption("8192", "8 GB"),
    MenuOption("16384", "16 GB"),
    MenuOption("32768", "32 GB"),
    MenuOption("65536", "64 GB"),
    MenuOption("ram", "Match RAM size"),
    MenuOption("custom", "Custom size"),
    MenuOption("0", "No swap"),
]

WIPE_OPTIONS: list[MenuOption] = [
    MenuOption("quick", "Quick wipe (partition table only)"),
    MenuOption("secure", "Secure wipe (random fill via shred or dd - slow)"),
    MenuOption("discard", "SSD discard (blkdiscard - fast, leaks patterns)"),
    MenuOption("skip", "Skip wipe (if you are recovering a partial install)"),
]


class HardwareDetector:
    """detects hardware information using command runner."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or SystemCommandRunner()

    def list_available_disks(self) -> list[DiskInfo]:
        """list all available disks on the system."""
        try:
            result = self._runner.run(
                ["lsblk", "-dno", "NAME,TYPE"],
                raise_on_nonzero_exit=False,
            )
            if not result.success:
                return []

            disks = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "disk":
                    disk_name = parts[0]
                    disk_path = f"/dev/{disk_name}"

                    model, size = self._get_disk_info(disk_path)
                    partitions = self._get_disk_partitions(disk_path)

                    disks.append(
                        DiskInfo(
                            name=disk_name,
                            path=disk_path,
                            model=model,
                            size=size,
                            partitions=partitions,
                        )
                    )

            return disks
        except Exception:
            return []

    def get_system_ram_mb(self) -> int:
        """get system RAM size in MB."""
        try:
            result = self._runner.run(
                ["grep", "MemTotal", "/proc/meminfo"],
                raise_on_nonzero_exit=False,
            )
            if result.success:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    kb = int(parts[1])
                    return kb // 1024
        except Exception:
            pass

        return 8192  # fallback to 8GB

    def _get_disk_info(self, disk_path: str) -> tuple[str, str]:
        """get model and size for a disk."""
        result = self._runner.run(
            ["lsblk", "-dno", "MODEL,SIZE", disk_path],
            raise_on_nonzero_exit=False,
        )
        if result.success:
            parts = result.stdout.strip().split(maxsplit=1)
            model = parts[0] if parts else "Unknown"
            size = parts[1] if len(parts) > 1 else "Unknown"
            return model, size
        return "Unknown", "Unknown"

    def _get_disk_partitions(self, disk_path: str) -> list[str]:
        """get partition info for a disk."""
        result = self._runner.run(
            ["lsblk", "-no", "NAME,SIZE,FSTYPE,MOUNTPOINT,UUID", disk_path],
            raise_on_nonzero_exit=False,
        )
        partitions = []
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    partitions.append(line.strip())
        return partitions


class InteractionStrategy(ABC):
    """abstract base class for user interaction strategies."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or SystemCommandRunner()
        self._hardware = HardwareDetector(self._runner)

    @abstractmethod
    def collect_all_selections(
        self,
        require_disk: bool = True,
        require_passwords: bool = True,
        require_system_config: bool = False,
    ) -> InstallationSelections:
        """collect all installation selections from user.

        args:
            require_disk: whether to prompt for disk selection
            require_passwords: whether to prompt for passwords
            require_system_config: whether to prompt for hostname/username/etc

        returns:
            complete installation selections
        """
        pass

    @abstractmethod
    def prompt_disk_selection(self) -> str:
        """prompt user to select target disk."""
        pass

    @abstractmethod
    def prompt_password(self, password_type: str, confirm: bool = True) -> str:
        """prompt user for a password."""
        pass

    @abstractmethod
    def prompt_gpu_vendor(self) -> str:
        """prompt user for GPU vendor selection."""
        pass

    @abstractmethod
    def prompt_cpu_vendor(self) -> str:
        """prompt user for CPU vendor selection."""
        pass

    @abstractmethod
    def prompt_desktop_environment(self) -> list[str]:
        """prompt user for desktop environment selection."""
        pass

    @abstractmethod
    def prompt_swap_size(self) -> int:
        """prompt user for swap size."""
        pass

    @abstractmethod
    def prompt_wipe_method(self) -> str:
        """prompt user for disk wipe method."""
        pass

    @abstractmethod
    def prompt_boolean(self, prompt_text: str, default: bool = False) -> bool:
        """prompt user for yes/no answer."""
        pass

    @abstractmethod
    def prompt_text(
        self,
        prompt_text: str,
        default: str = "",
        required: bool = False,
    ) -> str:
        """prompt user for text input."""
        pass

    @abstractmethod
    def prompt_secrets_key(self) -> str:
        """prompt for symmetric key to decrypt encrypted secrets."""
        pass

    @abstractmethod
    def prompt_nvidia_driver(self) -> str:
        """prompt for NVIDIA driver selection."""
        pass

    def get_available_disks(self) -> list[DiskInfo]:
        """get list of available disks."""
        return self._hardware.list_available_disks()

    def get_system_ram_mb(self) -> int:
        """get system RAM in MB."""
        return self._hardware.get_system_ram_mb()
