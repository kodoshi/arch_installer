from dataclasses import dataclass
from typing import Optional


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
    cpu_vendor: str = ""


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
class HibernationConfig:
    enabled: bool


@dataclass(frozen=True)
class SwapConfig:
    enabled: bool
    size_mb: int
    path: str
    hibernation: HibernationConfig


@dataclass(frozen=True)
class StorageConfig:
    target_disk: str
    efi_size_mb: int
    luks: LuksConfig
    btrfs: BtrfsConfig
    swap: SwapConfig
    wipe_method: str = "quick"


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
    hardening: Optional[CmdlineHardeningConfig]


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
    variants: tuple[UkiVariantConfig, ...]
    cmdline: CmdlineConfig
    loader: LoaderConfig
    hooks: tuple[str, ...]
    secure_boot: SecureBootConfig
    enable_snapshot_boot: bool


@dataclass(frozen=True)
class DesktopPackages:
    kde: tuple[str, ...]
    gnome: tuple[str, ...]
    hyprland: tuple[str, ...]


@dataclass(frozen=True)
class PackagesConfig:
    profile: str
    base: tuple[str, ...]
    desktops: DesktopPackages
    display_manager: tuple[str, ...]


@dataclass(frozen=True)
class GpuDriverPackages:
    amd: tuple[str, ...]
    intel: tuple[str, ...]
    nouveau: tuple[str, ...]
    nvidia_dkms: tuple[str, ...]
    nvidia_open: tuple[str, ...]


@dataclass(frozen=True)
class GpuConfig:
    enabled: bool
    vendor: str
    driver: str
    drivers: GpuDriverPackages


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
    timeline: bool
    cleanup: bool
    retention: SnapshotRetention


@dataclass(frozen=True)
class SnapPacConfig:
    enabled: bool


@dataclass(frozen=True)
class SnapperConfig:
    enabled: bool
    allow_groups: tuple[str, ...]
    root: Optional[SnapperVolumeConfig]
    home: Optional[SnapperVolumeConfig]
    snap_pac: SnapPacConfig
    notifications: bool = True


@dataclass(frozen=True)
class FirewallSshConfig:
    enabled: bool
    port: int
    allowed_from: Optional[str]


@dataclass(frozen=True)
class FirewallAllowRule:
    port: int
    protocol: str


@dataclass(frozen=True)
class FirewallConfig:
    enabled: bool
    default_incoming: str
    default_outgoing: str
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
class DotfilesConfig:
    enabled: bool
    remote_url: str
    repo_path: str


@dataclass(frozen=True)
class MigrationConfig:
    enabled: bool
    source_disk: str
    source_luks_password: str
    preserve_home: bool
    preserve_secure_boot_keys: bool
    preserve_ssh_keys: bool
    additional_paths: tuple[str, ...]


@dataclass(frozen=True)
class EncryptedSecretsConfig:
    luks_password_encrypted: str
    user_password_encrypted: str


@dataclass(frozen=True)
class DeclaredConfig:
    system: SystemConfig
    storage: StorageConfig
    boot: BootConfig
    packages: PackagesConfig
    gpu: GpuConfig
    snapper: SnapperConfig
    firewall: FirewallConfig
    docker: DockerConfig
    dotfiles: DotfilesConfig
    migration: MigrationConfig
    secrets: Optional[EncryptedSecretsConfig]
