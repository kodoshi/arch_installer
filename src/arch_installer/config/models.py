"""Frozen configuration model of the installer. No field has a default."""

from dataclasses import asdict, dataclass
from enum import Enum, StrEnum
from typing import Any


class WipeMethod(StrEnum):
    QUICK = "quick"
    SECURE = "secure"
    DISCARD = "discard"
    SKIP = "skip"


class CpuVendor(StrEnum):
    AMD = "amd"
    INTEL = "intel"
    UNKNOWN = ""


class GpuVendor(StrEnum):
    AMD = "amd"
    INTEL = "intel"
    NVIDIA = "nvidia"
    NONE = "none"


class GpuDriver(StrEnum):
    NOUVEAU = "nouveau"
    NVIDIA_OPEN = "nvidia-open"
    NVIDIA_DKMS = "nvidia-dkms"
    VENDOR_DEFAULT = ""


class FirewallPolicy(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REJECT = "reject"


class Desktop(StrEnum):
    KDE = "kde"
    GNOME = "gnome"
    HYPRLAND = "hyprland"


class BackupCategory(StrEnum):
    DOTFILES = "dotfiles"
    KEEPASS = "keepass"
    BROWSER = "browser"
    SYSTEM = "system"


CRYPTROOT_MAPPER_NAME = "cryptroot"


# any NVIDIA driver but nouveau is the proprietary one, which needs its own setup step
def is_proprietary_nvidia(vendor: str, driver: str) -> bool:
    return vendor == GpuVendor.NVIDIA and driver != GpuDriver.NOUVEAU


def derive_partition_path(disk: str, partition_number: int) -> str:
    if not disk:
        return ""
    # nvme and loop devices separate the partition number with a "p"
    if "nvme" in disk or "loop" in disk:
        return f"{disk}p{partition_number}"
    return f"{disk}{partition_number}"


@dataclass(frozen=True)
class UserConfig:
    name: str
    groups: tuple[str, ...]


@dataclass(frozen=True)
class LocaleConfig:
    language: str
    encoding: str
    keymap: str
    monetary: str
    time_format: str
    numeric: str
    paper: str

    @property
    def full_locale(self) -> str:
        return f"{self.language}.{self.encoding}"


@dataclass(frozen=True)
class PacmanMirrorConfig:
    # an empty list keeps the live ISO's mirrorlist
    mirrors: tuple[str, ...]
    use_reflector: bool
    reflector_countries: tuple[str, ...]


@dataclass(frozen=True)
class SystemConfig:
    hostname: str
    timezone: str
    locale: LocaleConfig
    user: UserConfig
    mirrors: PacmanMirrorConfig
    cpu_vendor: CpuVendor


@dataclass(frozen=True)
class LuksConfig:
    type: str
    cipher: str
    key_size: int
    hash: str
    pbkdf: str
    pbkdf_memory: int
    pbkdf_parallel: int
    pbkdf_time_ms: int


@dataclass(frozen=True)
class SubvolumeConfig:
    name: str
    mountpoint: str
    nocow: bool


@dataclass(frozen=True)
class BtrfsConfig:
    label: str
    mount_options: str
    subvolumes: tuple[SubvolumeConfig, ...]


@dataclass(frozen=True)
class SwapConfig:
    enabled: bool
    size_mb: int
    path: str
    hibernation: bool


@dataclass(frozen=True)
class StorageConfig:
    target_disk: str
    efi_size_mb: int
    wipe_method: WipeMethod
    luks: LuksConfig
    btrfs: BtrfsConfig
    swap: SwapConfig

    @property
    def cryptroot_device(self) -> str:
        return f"/dev/mapper/{CRYPTROOT_MAPPER_NAME}"


@dataclass(frozen=True)
class KernelConfig:
    name: str
    package: str


@dataclass(frozen=True)
class UkiVariantConfig:
    suffix: str
    params: str


@dataclass(frozen=True)
class CmdlineHardeningConfig:
    lockdown: str
    iommu: str
    intel_iommu: str
    amd_iommu: str
    pti: str
    spectre_v2: str
    spec_store_bypass_disable: str
    l1tf: str
    mds: str
    srbds: str
    tsx_async_abort: str
    init_on_alloc: int
    init_on_free: int


@dataclass(frozen=True)
class CmdlineConfig:
    rootflags: str
    rootfstype: str
    rw: bool
    quiet: bool
    hardening: CmdlineHardeningConfig


@dataclass(frozen=True)
class LoaderConfig:
    timeout: int
    console_mode: str
    editor: bool


@dataclass(frozen=True)
class SecureBootConfig:
    enroll_keys: bool
    include_microsoft_keys: bool


@dataclass(frozen=True)
class BootConfig:
    kernels: tuple[KernelConfig, ...]
    # kernel packages to install; an empty list installs every kernel listed above
    selected_kernels: tuple[str, ...]
    variants: tuple[UkiVariantConfig, ...]
    cmdline: CmdlineConfig
    loader: LoaderConfig
    hooks: tuple[str, ...]
    secure_boot: SecureBootConfig
    enable_snapshot_boot: bool

    @property
    def kernel_packages(self) -> tuple[str, ...]:
        if self.selected_kernels:
            return self.selected_kernels
        return tuple(kernel.package for kernel in self.kernels)


@dataclass(frozen=True)
class DesktopPackages:
    kde: tuple[str, ...]
    gnome: tuple[str, ...]
    hyprland: tuple[str, ...]

    def packages_for(self, desktop: Desktop) -> tuple[str, ...]:
        match desktop:
            case Desktop.KDE:
                return self.kde
            case Desktop.GNOME:
                return self.gnome
            case Desktop.HYPRLAND:
                return self.hyprland

    @property
    def configured(self) -> tuple[Desktop, ...]:
        return tuple(desktop for desktop in Desktop if self.packages_for(desktop))


@dataclass(frozen=True)
class PackagesConfig:
    base: tuple[str, ...]
    desktops: DesktopPackages
    # the desktops to install; each needs packages listed above
    selected_desktops: tuple[Desktop, ...]
    display_manager: tuple[str, ...]
    # package names exported by a USB backup of a previous system
    cataloged: tuple[str, ...]


@dataclass(frozen=True)
class GpuDriverPackages:
    amd: tuple[str, ...]
    intel: tuple[str, ...]
    nouveau: tuple[str, ...]
    nvidia_dkms: tuple[str, ...]
    nvidia_open: tuple[str, ...]


@dataclass(frozen=True)
class GpuConfig:
    vendor: GpuVendor
    driver: GpuDriver
    drivers: GpuDriverPackages

    @property
    def uses_proprietary_nvidia_driver(self) -> bool:
        return is_proprietary_nvidia(self.vendor, self.driver)


@dataclass(frozen=True)
class SnapshotRetention:
    hourly: int
    daily: int
    weekly: int
    monthly: int
    yearly: int


@dataclass(frozen=True)
class SnapperVolumeConfig:
    subvolume: str
    snapshots_subvolume: str
    snapshots_path: str
    timeline: bool
    cleanup: bool
    number_limit: int
    number_limit_important: int
    retention: SnapshotRetention


@dataclass(frozen=True)
class SnapperConfig:
    enabled: bool
    allow_groups: tuple[str, ...]
    # null turns snapshots of that volume off
    root: SnapperVolumeConfig | None
    home: SnapperVolumeConfig | None
    # pre/post snapshots around every pacman transaction
    snap_pac: bool
    # how many of the newest snapshots get a bootable UKI
    bootable_snapshot_count: int

    @property
    def volumes(self) -> dict[str, SnapperVolumeConfig]:
        configured = {"root": self.root, "home": self.home}
        return {name: volume for name, volume in configured.items() if volume is not None}


@dataclass(frozen=True)
class NotificationsConfig:
    enabled: bool


@dataclass(frozen=True)
class FirewallSshConfig:
    enabled: bool
    port: int
    allowed_from: str


@dataclass(frozen=True)
class FirewallAllowRule:
    port: int
    protocol: str


@dataclass(frozen=True)
class FirewallConfig:
    enabled: bool
    default_incoming: FirewallPolicy
    default_outgoing: FirewallPolicy
    logging: bool
    block_icmp: bool
    ssh: FirewallSshConfig
    allow_rules: tuple[FirewallAllowRule, ...]


@dataclass(frozen=True)
class DockerConfig:
    enabled: bool
    storage_driver: str
    data_root: str
    access_group: str


@dataclass(frozen=True)
class BackupItemConfig:
    name: str
    source_path: str
    description: str


@dataclass(frozen=True)
class SyncConfig:
    backup_partition: str
    backup_items: tuple[BackupItemConfig, ...]
    backup_categories: tuple[BackupCategory, ...]


@dataclass(frozen=True)
class MigrationConfig:
    enabled: bool
    preserve_home: bool
    preserve_secure_boot_keys: bool
    additional_paths: tuple[str, ...]


# the USB boot drive layout. its EFI partition is the system's only one and its header
# partition holds the LUKS header, so the internal disk keeps nothing but ciphertext
USB_EFI_PARTITION_NUMBER = 1
USB_LUKS_HEADER_PARTITION_NUMBER = 2
USB_RECOVERY_PARTITION_NUMBER = 3


@dataclass(frozen=True)
class UsbBootConfig:
    enabled: bool
    device: str
    recovery_system: bool
    iso_path: str

    @property
    def efi_partition(self) -> str:
        return derive_partition_path(self.device, USB_EFI_PARTITION_NUMBER)

    @property
    def luks_header_partition(self) -> str:
        return derive_partition_path(self.device, USB_LUKS_HEADER_PARTITION_NUMBER)

    @property
    def recovery_partition(self) -> str:
        return derive_partition_path(self.device, USB_RECOVERY_PARTITION_NUMBER)


# the credential each encrypted secret holds; bound into its ciphertext, so the two
# encrypted passwords cannot be swapped
LUKS_PASSWORD_SECRET = "luks_password"
USER_PASSWORD_SECRET = "user_password"


@dataclass(frozen=True)
class EncryptedSecretsConfig:
    luks_password_encrypted: str
    user_password_encrypted: str

    @property
    def configured(self) -> bool:
        return bool(self.luks_password_encrypted or self.user_password_encrypted)


@dataclass(frozen=True)
class Credentials:
    luks_password: str
    user_password: str
    # only needed to unlock an existing install when migrating
    source_luks_password: str

    def __repr__(self) -> str:
        # keeps passwords out of tracebacks and debug output
        return "Credentials(<hidden>)"


@dataclass(frozen=True)
class InstallerConfig:
    system: SystemConfig
    packages: PackagesConfig
    storage: StorageConfig
    boot: BootConfig
    gpu: GpuConfig
    snapper: SnapperConfig
    firewall: FirewallConfig
    docker: DockerConfig
    notifications: NotificationsConfig
    sync: SyncConfig
    migration: MigrationConfig
    usb_boot: UsbBootConfig
    secrets: EncryptedSecretsConfig
    # never read from config.yaml in plain text: env vars, the TUI or decrypted secrets
    credentials: Credentials

    # the device the ciphertext fills: with a USB boot drive the whole internal disk, which
    # then has no partition table, otherwise the disk's second partition
    @property
    def encrypted_device(self) -> str:
        if self.usb_boot.enabled:
            return self.storage.target_disk
        return derive_partition_path(self.storage.target_disk, 2)

    @property
    def efi_partition(self) -> str:
        if self.usb_boot.enabled:
            return self.usb_boot.efi_partition
        return derive_partition_path(self.storage.target_disk, 1)

    # cryptsetup reads the LUKS header from here: the USB drive, or the partition itself
    @property
    def luks_header_device(self) -> str:
        if self.usb_boot.enabled:
            return self.usb_boot.luks_header_partition
        return self.encrypted_device


def _plain_yaml_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain_yaml_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_yaml_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def exportable_config(config: InstallerConfig) -> dict[str, Any]:
    # the shape of config.yaml, without passwords: final_config.yaml and USB backups
    exported = _plain_yaml_value(asdict(config))
    del exported["credentials"]
    del exported["secrets"]
    return exported
