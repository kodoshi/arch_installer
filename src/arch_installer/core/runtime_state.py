from dataclasses import dataclass, field
from pathlib import Path


def _derive_partition_path(disk: str, partition_num: int) -> str:
    """derive partition path from disk path."""
    if not disk:
        return ""
    # nvme and loop devices use 'p' separator
    if "nvme" in disk or "loop" in disk:
        return f"{disk}p{partition_num}"
    return f"{disk}{partition_num}"


@dataclass
class RuntimeConfig:
    """
    mutable runtime configuration populated during installation.

    this is separate from DeclaredConfig which is the immutable
    source of truth from config.yaml.

    RuntimeConfig holds values that are determined at runtime,
    like passwords, detected hardware, user selections during interactive mode, etc.
    """

    target_disk: str = ""
    luks_password: str = ""
    user_password: str = ""
    source_luks_password: str = ""
    target_root: Path = field(default_factory=lambda: Path("/mnt"))
    efi_mount: Path = field(default_factory=lambda: Path("/mnt/efi"))

    non_interactive: bool = False
    enable_snapshot_boot: bool = False
    enable_firewall: bool = True
    enable_hibernation: bool = False
    enable_docker: bool = False
    enable_migration: bool = False
    skip_swap: bool = False
    wipe_method: str = "quick"

    hostname: str = ""
    username: str = ""
    timezone: str = ""

    excluded_steps: set = field(default_factory=set)

    selected_kernels: list[str] = field(default_factory=list)
    selected_desktops: list[str] = field(default_factory=list)
    selected_debug_variants: list[str] = field(default_factory=list)

    cpu_vendor: str = ""
    gpu_vendor: str = ""
    gpu_driver: str = ""

    package_profile: str = "base"
    swap_size_mb: int = 0

    # optional overrides for partition paths
    efi_partition_override: str = ""
    root_partition_override: str = ""

    @property
    def efi_partition(self) -> str:
        if self.efi_partition_override:
            return self.efi_partition_override
        return _derive_partition_path(self.target_disk, 1)

    @efi_partition.setter
    def efi_partition(self, value: str) -> None:
        self.efi_partition_override = value

    @property
    def root_partition(self) -> str:
        if self.root_partition_override:
            return self.root_partition_override
        return _derive_partition_path(self.target_disk, 2)

    @root_partition.setter
    def root_partition(self, value: str) -> None:
        self.root_partition_override = value

    @property
    def cryptroot_device(self) -> str:
        return "/dev/mapper/cryptroot"


def create_default_runtime_config() -> RuntimeConfig:
    return RuntimeConfig()
