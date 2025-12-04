from pathlib import Path
from typing import Any

import yaml

from arch_installer.errors import ConfigurationError

from .models import (
    BootConfig,
    BtrfsConfig,
    CmdlineConfig,
    CmdlineHardeningConfig,
    DeclaredConfig,
    DesktopPackages,
    DockerConfig,
    DotfilesConfig,
    EncryptedSecretsConfig,
    FirewallAllowRule,
    FirewallConfig,
    FirewallSshConfig,
    GpuConfig,
    GpuDriverPackages,
    HibernationConfig,
    KernelConfig,
    LoaderConfig,
    LocaleConfig,
    LuksConfig,
    MigrationConfig,
    PackagesConfig,
    PacmanMirrorConfig,
    SecureBootConfig,
    SnapPacConfig,
    SnapperConfig,
    SnapperVolumeConfig,
    SnapshotRetention,
    StorageConfig,
    SubvolumeConfig,
    SwapConfig,
    SystemConfig,
    UkiVariantConfig,
    UserConfig,
)
from .validator import validate_config_or_raise


def load_config_with_validation(
    config_path: str | Path | None = None,
    non_interactive: bool = False,
) -> DeclaredConfig:
    """load and validate config.yaml.

    args:
        config_path: path to config file (defaults to config/config.yaml)
        non_interactive: if True, validates for non-interactive mode

    returns:
        validated DeclaredConfig

    raises:
        ConfigurationError: if config is invalid
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent.parent / "config" / "config.yaml"

    validate_config_or_raise(config_path, non_interactive)
    return load_main_yaml_config(config_path)


def load_main_yaml_config(config_path: str | Path | None = None) -> DeclaredConfig:
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent.parent / "config" / "config.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigurationError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path) as file_handle:
            raw = yaml.safe_load(file_handle)
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Failed to parse configuration: {error}") from error

    return DeclaredConfig(
        system=_parse_system_section(raw.get("system", {})),
        storage=_parse_storage_section(raw.get("storage", {})),
        boot=_parse_boot_section(raw.get("boot", {})),
        packages=_parse_packages_section(raw.get("packages", {})),
        gpu=_parse_gpu_section(raw.get("gpu", {})),
        snapper=_parse_snapper_section(raw.get("snapper"), section_present="snapper" in raw),
        firewall=_parse_firewall_section(raw.get("firewall"), section_present="firewall" in raw),
        docker=_parse_docker_section(raw.get("docker"), section_present="docker" in raw),
        dotfiles=_parse_dotfiles_section(raw.get("dotfiles"), section_present="dotfiles" in raw),
        migration=_parse_migration_section(raw.get("migration", {})),
        secrets=_parse_secrets_section(raw.get("secrets")),
    )


def _parse_system_section(raw_config: dict[str, Any]) -> SystemConfig:
    user_raw = raw_config.get("user", {})
    user = UserConfig(
        name=user_raw.get("name", "user"),
        groups=tuple(user_raw.get("groups", ["wheel"])),
    )

    locale_raw = raw_config.get("locale", {})
    locale = LocaleConfig(
        language=locale_raw.get("language", "en_US"),
        encoding=locale_raw.get("encoding", "UTF-8"),
        keymap=locale_raw.get("keymap", "us"),
        monetary=locale_raw.get("monetary", "en_US.UTF-8"),
        time_format=locale_raw.get("time_format", "en_US.UTF-8"),
        numeric=locale_raw.get("numeric", "en_US.UTF-8"),
        paper=locale_raw.get("paper", "en_US.UTF-8"),
    )

    mirrors_raw = raw_config.get("mirrors", {})
    mirrors = PacmanMirrorConfig(
        mirrors=tuple(mirrors_raw.get("mirrors", [])),
        use_reflector=mirrors_raw.get("use_reflector", False),
        reflector_countries=tuple(
            mirrors_raw.get("reflector_countries", ["France", "Germany", "Netherlands"])
        ),
    )

    return SystemConfig(
        hostname=raw_config["hostname"],
        timezone=raw_config["timezone"],
        locale=locale,
        user=user,
        mirrors=mirrors,
        cpu_vendor=raw_config.get("cpu_vendor", ""),
    )


def _parse_storage_section(raw_config: dict[str, Any]) -> StorageConfig:
    luks_raw = raw_config.get("luks", {})
    luks = LuksConfig(
        type=luks_raw.get("type", "luks2"),
        cipher=luks_raw.get("cipher", "aes-xts-plain64"),
        key_size=luks_raw.get("key_size", 512),
        hash=luks_raw.get("hash", "sha512"),
        pbkdf=luks_raw.get("pbkdf", "argon2id"),
        pbkdf_memory=luks_raw.get("pbkdf_memory", 1048576),
        pbkdf_parallel=luks_raw.get("pbkdf_parallel", 4),
        pbkdf_time_ms=luks_raw.get("pbkdf_time_ms", 4000),
    )

    btrfs_raw = raw_config.get("btrfs", {})
    subvolumes = tuple(
        SubvolumeConfig(
            name=subvolume.get("name", ""),
            mountpoint=subvolume.get("mountpoint", ""),
            nocow=subvolume.get("nocow", False),
        )
        for subvolume in btrfs_raw.get("subvolumes", [])
    )
    btrfs = BtrfsConfig(
        label=btrfs_raw.get("label", "archroot"),
        mount_options=btrfs_raw.get("mount_options", "compress=zstd,noatime"),
        subvolumes=subvolumes,
    )

    swap_raw = raw_config.get("swap", {})
    hibernation_raw = swap_raw.get("hibernation", {})
    hibernation = HibernationConfig(
        enabled=hibernation_raw.get("enabled", False),
    )
    swap = SwapConfig(
        enabled=swap_raw.get("enabled", True),
        size_mb=swap_raw.get("size_mb", 32768),
        path=swap_raw.get("path", "/.swap/swapfile"),
        hibernation=hibernation,
    )

    return StorageConfig(
        target_disk=raw_config.get("target_disk", ""),
        efi_size_mb=raw_config.get("efi_size_mb", 2048),
        luks=luks,
        btrfs=btrfs,
        swap=swap,
        wipe_method=raw_config.get("wipe_method", "quick"),
    )


def _parse_boot_section(raw_config: dict[str, Any]) -> BootConfig:
    kernels = tuple(
        KernelConfig(
            name=kernel_entry.get("name", ""),
            package=kernel_entry.get("package", ""),
        )
        for kernel_entry in raw_config.get("kernels", [])
    )

    variants = tuple(
        UkiVariantConfig(
            suffix=variant.get("suffix", ""),
            params=variant.get("params", ""),
        )
        for variant in raw_config.get("variants", [])
    )

    cmdline_raw = raw_config.get("cmdline", {})
    hardening_raw = cmdline_raw.get("hardening")
    hardening = (
        CmdlineHardeningConfig(
            lockdown=hardening_raw.get("lockdown", "integrity"),
            iommu=hardening_raw.get("iommu", "force"),
            intel_iommu=hardening_raw.get("intel_iommu", "on"),
            amd_iommu=hardening_raw.get("amd_iommu", "force_isolation"),
            pti=hardening_raw.get("pti", "on"),
            spectre_v2=hardening_raw.get("spectre_v2", "on"),
            spec_store_bypass_disable=hardening_raw.get("spec_store_bypass_disable", "on"),
            l1tf=hardening_raw.get("l1tf", "full,force"),
            mds=hardening_raw.get("mds", "full,nosmt"),
            srbds=hardening_raw.get("srbds", "on"),
            tsx_async_abort=hardening_raw.get("tsx_async_abort", "full,nosmt"),
            init_on_alloc=hardening_raw.get("init_on_alloc", 1),
            init_on_free=hardening_raw.get("init_on_free", 1),
        )
        if hardening_raw
        else None
    )

    cmdline = CmdlineConfig(
        rootflags=cmdline_raw.get("rootflags", "subvol=@"),
        rootfstype=cmdline_raw.get("rootfstype", "btrfs"),
        rw=cmdline_raw.get("rw", True),
        quiet=cmdline_raw.get("quiet", True),
        hardening=hardening,
    )

    loader_raw = raw_config.get("loader", {})
    loader = LoaderConfig(
        timeout=loader_raw.get("timeout", 20),
        console_mode=loader_raw.get("console_mode", "max"),
        editor=loader_raw.get("editor", False),
    )

    hooks = tuple(raw_config.get("hooks", []))

    secure_boot_raw = raw_config.get("secure_boot", {})
    secure_boot = SecureBootConfig(
        enroll_keys=secure_boot_raw.get("enroll_keys", True),
        include_microsoft_keys=secure_boot_raw.get("include_microsoft_keys", True),
    )

    return BootConfig(
        kernels=kernels,
        variants=variants,
        cmdline=cmdline,
        loader=loader,
        hooks=hooks,
        secure_boot=secure_boot,
        enable_snapshot_boot=raw_config.get("enable_snapshot_boot", False),
    )


def _parse_packages_section(raw_config: dict[str, Any]) -> PackagesConfig:
    desktops_raw = raw_config.get("desktops", {})
    desktops = DesktopPackages(
        kde=tuple(desktops_raw.get("kde", [])),
        gnome=tuple(desktops_raw.get("gnome", [])),
        hyprland=tuple(desktops_raw.get("hyprland", [])),
    )

    return PackagesConfig(
        profile=raw_config.get("profile", "base"),
        base=tuple(raw_config.get("base", [])),
        desktops=desktops,
        display_manager=tuple(raw_config.get("display_manager", [])),
    )


def _parse_gpu_section(raw_config: dict[str, Any]) -> GpuConfig:
    drivers_raw = raw_config.get("drivers", {})
    drivers = GpuDriverPackages(
        amd=tuple(drivers_raw.get("amd", [])),
        intel=tuple(drivers_raw.get("intel", [])),
        nouveau=tuple(drivers_raw.get("nouveau", [])),
        nvidia_dkms=tuple(drivers_raw.get("nvidia_dkms", [])),
        nvidia_open=tuple(drivers_raw.get("nvidia_open", [])),
    )

    return GpuConfig(
        enabled=raw_config.get("enabled", False),
        vendor=raw_config.get("vendor", "none"),
        driver=raw_config.get("driver", ""),
        drivers=drivers,
    )


def _parse_snapper_section(
    raw: dict[str, Any] | None,
    section_present: bool = True,
) -> SnapperConfig:
    if raw is None:
        raw = {}

    # if section is not present, disable by default
    default_enabled = section_present

    root_raw = raw.get("root")
    root_volume = None
    if root_raw:
        root_snapshot_retention = SnapshotRetention(
            hourly=root_raw.get("retention", {}).get("hourly", 5),
            daily=root_raw.get("retention", {}).get("daily", 7),
            weekly=root_raw.get("retention", {}).get("weekly", 4),
            monthly=root_raw.get("retention", {}).get("monthly", 6),
            yearly=root_raw.get("retention", {}).get("yearly", 2),
        )
        root_volume = SnapperVolumeConfig(
            subvolume=root_raw.get("subvolume", "/"),
            timeline=root_raw.get("timeline", True),
            cleanup=root_raw.get("cleanup", True),
            retention=root_snapshot_retention,
        )

    home_raw = raw.get("home")
    home_volume = None
    if home_raw:
        home_snapshot_retention = SnapshotRetention(
            hourly=home_raw.get("retention", {}).get("hourly", 5),
            daily=home_raw.get("retention", {}).get("daily", 7),
            weekly=home_raw.get("retention", {}).get("weekly", 4),
            monthly=home_raw.get("retention", {}).get("monthly", 3),
            yearly=home_raw.get("retention", {}).get("yearly", 1),
        )
        home_volume = SnapperVolumeConfig(
            subvolume=home_raw.get("subvolume", "/home"),
            timeline=home_raw.get("timeline", True),
            cleanup=home_raw.get("cleanup", True),
            retention=home_snapshot_retention,
        )

    snap_pac_raw = raw.get("snap_pac", {})
    snap_pac = SnapPacConfig(enabled=snap_pac_raw.get("enabled", True))

    return SnapperConfig(
        enabled=raw.get("enabled", default_enabled),
        allow_groups=tuple(raw.get("allow_groups", ["wheel"])),
        root=root_volume,
        home=home_volume,
        snap_pac=snap_pac,
        notifications=raw.get("notifications", True),
    )


def _parse_firewall_section(
    raw: dict[str, Any] | None,
    section_present: bool = True,
) -> FirewallConfig:
    if raw is None:
        raw = {}

    default_enabled = section_present

    ssh_raw = raw.get("ssh", {})
    ssh = FirewallSshConfig(
        enabled=ssh_raw.get("enabled", False),
        port=ssh_raw.get("port", 22),
        allowed_from=ssh_raw.get("allowed_from"),
    )

    allow_rules_raw = raw.get("allow_rules", [])
    allow_rules = tuple(
        FirewallAllowRule(
            port=rule.get("port", 0),
            protocol=rule.get("protocol", "tcp"),
        )
        for rule in allow_rules_raw
    )

    return FirewallConfig(
        enabled=raw.get("enabled", default_enabled),
        default_incoming=raw.get("default_incoming", "deny"),
        default_outgoing=raw.get("default_outgoing", "allow"),
        logging=raw.get("logging", True),
        block_icmp=raw.get("block_icmp", True),
        ssh=ssh,
        allow_rules=allow_rules,
    )


def _parse_docker_section(
    raw: dict[str, Any] | None,
    section_present: bool = True,
) -> DockerConfig:
    if raw is None:
        raw = {}

    default_enabled = section_present

    return DockerConfig(
        enabled=raw.get("enabled", default_enabled),
        storage_driver=raw.get("storage_driver", "overlay2"),
        data_root=raw.get("data_root", "/var/lib/docker"),
        access_group=raw.get("access_group", "docker_access"),
    )


def _parse_dotfiles_section(
    raw: dict[str, Any] | None,
    section_present: bool = True,
) -> DotfilesConfig:
    if raw is None:
        raw = {}

    default_enabled = section_present and bool(raw.get("remote_url"))

    return DotfilesConfig(
        enabled=raw.get("enabled", default_enabled),
        remote_url=raw.get("remote_url", ""),
        repo_path=raw.get("repo_path", "~/.dotfiles-repo"),
    )


def _parse_migration_section(raw: dict[str, Any]) -> MigrationConfig:
    return MigrationConfig(
        enabled=raw.get("enabled", False),
        source_disk=raw.get("source_disk", ""),
        source_luks_password=raw.get("source_luks_password", ""),
        preserve_home=raw.get("preserve_home", True),
        preserve_secure_boot_keys=raw.get("preserve_secure_boot_keys", True),
        preserve_ssh_keys=raw.get("preserve_ssh_keys", True),
        additional_paths=tuple(raw.get("additional_paths", [])),
    )


def _parse_secrets_section(raw: dict[str, Any] | None) -> EncryptedSecretsConfig | None:
    """parse the standalone secrets section for encrypted passwords."""
    if raw is None:
        return None

    luks_encrypted = raw.get("luks_password_encrypted", "")
    user_encrypted = raw.get("user_password_encrypted", "")

    # only return a config if at least one password is encrypted
    if not luks_encrypted and not user_encrypted:
        return None

    return EncryptedSecretsConfig(
        luks_password_encrypted=luks_encrypted,
        user_password_encrypted=user_encrypted,
    )
