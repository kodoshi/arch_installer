"""the installer's single configuration model.

every default value lives here, as a dataclass field default: the YAML loader only
fills in what config.yaml declares, environment variables and TUI answers only
override individual settings. nothing else in the code base carries a default.
"""

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


def derive_partition_path(disk: str, partition_number: int) -> str:
    if not disk:
        return ""
    # nvme and loop devices separate the partition number with a "p"
    if "nvme" in disk or "loop" in disk:
        return f"{disk}p{partition_number}"
    return f"{disk}{partition_number}"


@dataclass(frozen=True)
class UserConfig:
    name: str = "user"
    groups: tuple[str, ...] = ("wheel",)


@dataclass(frozen=True)
class LocaleConfig:
    language: str = "en_US"
    encoding: str = "UTF-8"
    keymap: str = "us"
    monetary: str = "en_US.UTF-8"
    time_format: str = "en_US.UTF-8"
    numeric: str = "en_US.UTF-8"
    paper: str = "en_US.UTF-8"

    @property
    def full_locale(self) -> str:
        return f"{self.language}.{self.encoding}"


@dataclass(frozen=True)
class PacmanMirrorConfig:
    # an empty list keeps the live ISO's mirrorlist
    mirrors: tuple[str, ...] = ()
    use_reflector: bool = False
    reflector_countries: tuple[str, ...] = ("France", "Germany", "Netherlands")


@dataclass(frozen=True)
class SystemConfig:
    hostname: str
    timezone: str
    locale: LocaleConfig = LocaleConfig()
    user: UserConfig = UserConfig()
    mirrors: PacmanMirrorConfig = PacmanMirrorConfig()
    cpu_vendor: CpuVendor = CpuVendor.UNKNOWN


@dataclass(frozen=True)
class LuksConfig:
    type: str = "luks2"
    cipher: str = "aes-xts-plain64"
    key_size: int = 512
    hash: str = "sha512"
    pbkdf: str = "argon2id"
    pbkdf_memory: int = 1048576
    pbkdf_parallel: int = 4
    pbkdf_time_ms: int = 4000


@dataclass(frozen=True)
class SubvolumeConfig:
    name: str
    mountpoint: str
    nocow: bool = False


@dataclass(frozen=True)
class BtrfsConfig:
    label: str = "archroot"
    mount_options: str = "compress=zstd,noatime"
    subvolumes: tuple[SubvolumeConfig, ...] = (
        SubvolumeConfig("@", "/"),
        SubvolumeConfig("@home", "/home"),
        SubvolumeConfig("@home-snapshots", "/home/.snapshots"),
        SubvolumeConfig("@var-log", "/var/log"),
        SubvolumeConfig("@snapshots", "/.snapshots"),
        SubvolumeConfig("@swap", "/.swap", nocow=True),
    )


@dataclass(frozen=True)
class SwapConfig:
    enabled: bool = True
    size_mb: int = 32768
    path: str = "/.swap/swapfile"
    hibernation: bool = False


@dataclass(frozen=True)
class StorageConfig:
    target_disk: str = ""
    efi_size_mb: int = 2048
    wipe_method: WipeMethod = WipeMethod.QUICK
    luks: LuksConfig = LuksConfig()
    btrfs: BtrfsConfig = BtrfsConfig()
    swap: SwapConfig = SwapConfig()

    @property
    def efi_partition(self) -> str:
        return derive_partition_path(self.target_disk, 1)

    @property
    def root_partition(self) -> str:
        return derive_partition_path(self.target_disk, 2)

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
    params: str = ""


@dataclass(frozen=True)
class CmdlineHardeningConfig:
    lockdown: str = "integrity"
    iommu: str = "force"
    intel_iommu: str = "on"
    amd_iommu: str = "force_isolation"
    pti: str = "on"
    spectre_v2: str = "on"
    spec_store_bypass_disable: str = "on"
    l1tf: str = "full,force"
    mds: str = "full,nosmt"
    srbds: str = "on"
    tsx_async_abort: str = "full,nosmt"
    init_on_alloc: int = 1
    init_on_free: int = 1


@dataclass(frozen=True)
class CmdlineConfig:
    rootflags: str = "subvol=@"
    rootfstype: str = "btrfs"
    rw: bool = True
    quiet: bool = True
    hardening: CmdlineHardeningConfig = CmdlineHardeningConfig()


@dataclass(frozen=True)
class LoaderConfig:
    timeout: int = 20
    console_mode: str = "max"
    editor: bool = False


@dataclass(frozen=True)
class SecureBootConfig:
    enroll_keys: bool = True
    include_microsoft_keys: bool = True


@dataclass(frozen=True)
class BootConfig:
    kernels: tuple[KernelConfig, ...] = (KernelConfig("mainline", "linux"),)
    # empty means: install every kernel listed above
    selected_kernels: tuple[str, ...] = ()
    variants: tuple[UkiVariantConfig, ...] = ()
    cmdline: CmdlineConfig = CmdlineConfig()
    loader: LoaderConfig = LoaderConfig()
    hooks: tuple[str, ...] = (
        "systemd",
        "autodetect",
        "microcode",
        "modconf",
        "kms",
        "keyboard",
        "sd-vconsole",
        "block",
        "sd-encrypt",
        "filesystems",
        "fsck",
    )
    secure_boot: SecureBootConfig = SecureBootConfig()
    enable_snapshot_boot: bool = False

    @property
    def kernel_packages(self) -> tuple[str, ...]:
        if self.selected_kernels:
            return self.selected_kernels
        return tuple(kernel.package for kernel in self.kernels)


@dataclass(frozen=True)
class DesktopPackages:
    kde: tuple[str, ...] = ()
    gnome: tuple[str, ...] = ()
    hyprland: tuple[str, ...] = ()

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
    desktops: DesktopPackages = DesktopPackages()
    # None means: every desktop that has packages listed above
    selected_desktops: tuple[Desktop, ...] | None = None
    display_manager: tuple[str, ...] = ()
    # package names exported by a USB backup of a previous system
    cataloged: tuple[str, ...] = ()

    @property
    def desktops_to_install(self) -> tuple[Desktop, ...]:
        if self.selected_desktops is None:
            return self.desktops.configured
        return self.selected_desktops


@dataclass(frozen=True)
class GpuDriverPackages:
    amd: tuple[str, ...] = ()
    intel: tuple[str, ...] = ()
    nouveau: tuple[str, ...] = ()
    nvidia_dkms: tuple[str, ...] = ()
    nvidia_open: tuple[str, ...] = ()


@dataclass(frozen=True)
class GpuConfig:
    vendor: GpuVendor = GpuVendor.NONE
    driver: GpuDriver = GpuDriver.VENDOR_DEFAULT
    drivers: GpuDriverPackages = GpuDriverPackages()

    @property
    def uses_proprietary_nvidia_driver(self) -> bool:
        return self.vendor == GpuVendor.NVIDIA and self.driver != GpuDriver.NOUVEAU


@dataclass(frozen=True)
class SnapshotRetention:
    hourly: int = 5
    daily: int = 7
    weekly: int = 4
    monthly: int = 6
    yearly: int = 2


@dataclass(frozen=True)
class SnapperVolumeConfig:
    subvolume: str
    snapshots_subvolume: str
    snapshots_path: str
    timeline: bool = True
    cleanup: bool = True
    number_limit: int = 10
    number_limit_important: int = 5
    retention: SnapshotRetention = SnapshotRetention()


@dataclass(frozen=True)
class SnapperConfig:
    enabled: bool = True
    allow_groups: tuple[str, ...] = ("wheel",)
    root: SnapperVolumeConfig | None = SnapperVolumeConfig(
        subvolume="/", snapshots_subvolume="@snapshots", snapshots_path="/.snapshots"
    )
    home: SnapperVolumeConfig | None = SnapperVolumeConfig(
        subvolume="/home",
        snapshots_subvolume="@home-snapshots",
        snapshots_path="/home/.snapshots",
        number_limit=5,
        number_limit_important=3,
        retention=SnapshotRetention(monthly=3, yearly=1),
    )
    # pre/post snapshots around every pacman transaction
    snap_pac: bool = True
    # how many of the newest snapshots get a bootable UKI
    bootable_snapshot_count: int = 5

    @property
    def volumes(self) -> dict[str, SnapperVolumeConfig]:
        configured = {"root": self.root, "home": self.home}
        return {name: volume for name, volume in configured.items() if volume is not None}


@dataclass(frozen=True)
class NotificationsConfig:
    enabled: bool = True


@dataclass(frozen=True)
class FirewallSshConfig:
    enabled: bool = False
    port: int = 22
    allowed_from: str = ""


@dataclass(frozen=True)
class FirewallAllowRule:
    port: int
    protocol: str = "tcp"


@dataclass(frozen=True)
class FirewallConfig:
    enabled: bool = True
    default_incoming: FirewallPolicy = FirewallPolicy.DENY
    default_outgoing: FirewallPolicy = FirewallPolicy.ALLOW
    logging: bool = True
    block_icmp: bool = True
    ssh: FirewallSshConfig = FirewallSshConfig()
    allow_rules: tuple[FirewallAllowRule, ...] = ()


@dataclass(frozen=True)
class DockerConfig:
    enabled: bool = False
    storage_driver: str = "overlay2"
    data_root: str = "/var/lib/docker"
    access_group: str = "docker_access"


@dataclass(frozen=True)
class BackupItemConfig:
    name: str
    source_path: str
    description: str = ""


@dataclass(frozen=True)
class SyncConfig:
    backup_items: tuple[BackupItemConfig, ...] = ()
    backup_categories: tuple[BackupCategory, ...] = tuple(BackupCategory)


@dataclass(frozen=True)
class MigrationConfig:
    enabled: bool = False
    preserve_home: bool = True
    preserve_secure_boot_keys: bool = True
    additional_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class UsbBootConfig:
    enabled: bool = False
    device: str = ""
    efi_size_mb: int = 512
    iso_partition_size_mb: int = 1024
    iso_path: str = ""
    detached_luks_header: bool = True
    backup_partition_size_mb: int = 0


@dataclass(frozen=True)
class EncryptedSecretsConfig:
    luks_password_encrypted: str = ""
    user_password_encrypted: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.luks_password_encrypted or self.user_password_encrypted)


@dataclass(frozen=True)
class Credentials:
    luks_password: str = ""
    user_password: str = ""
    # only needed to unlock an existing install when migrating
    source_luks_password: str = ""

    def __repr__(self) -> str:
        # keeps passwords out of tracebacks and debug output
        return "Credentials(<hidden>)"


@dataclass(frozen=True)
class InstallerConfig:
    system: SystemConfig
    packages: PackagesConfig
    storage: StorageConfig = StorageConfig()
    boot: BootConfig = BootConfig()
    gpu: GpuConfig = GpuConfig()
    snapper: SnapperConfig = SnapperConfig()
    firewall: FirewallConfig = FirewallConfig()
    docker: DockerConfig = DockerConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    sync: SyncConfig = SyncConfig()
    migration: MigrationConfig = MigrationConfig()
    usb_boot: UsbBootConfig = UsbBootConfig()
    secrets: EncryptedSecretsConfig = EncryptedSecretsConfig()
    # never read from config.yaml in plain text: env vars, the TUI or decrypted secrets
    credentials: Credentials = Credentials()


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
