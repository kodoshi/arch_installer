"""everything the installer can do, in the order it does it.

each step lists the settings that belong to it: their config.yaml key, the environment
variable that can provide them and the question the interactive setup asks. a setting is
listed once, with its step; any step may still read any setting from the finished
InstallerConfig. the order of INSTALL_STEPS is the order the steps run and the order the
interactive setup asks its questions in.
"""

from enum import StrEnum
from typing import Any

from arch_installer.config.environment import EnvVariable
from arch_installer.config.models import (
    CpuVendor,
    Desktop,
    GpuDriver,
    GpuVendor,
    WipeMethod,
    is_proprietary_nvidia,
)
from arch_installer.executors.boot import BootloaderStepExecutor, KernelImagesStepExecutor
from arch_installer.executors.docker import DockerStepExecutor
from arch_installer.executors.firewall import FirewallStepExecutor
from arch_installer.executors.gpu import GpuDriverStepExecutor
from arch_installer.executors.migration import (
    MigrationRestoreStepExecutor,
    MigrationStagingStepExecutor,
)
from arch_installer.executors.mirrors import PacmanMirrorsStepExecutor
from arch_installer.executors.packages import PackagesStepExecutor
from arch_installer.executors.recovery import RecoverySystemStepExecutor
from arch_installer.executors.snapper import (
    BootableSnapshotsStepExecutor,
    SnapperStepExecutor,
    SnapshotNotificationsStepExecutor,
)
from arch_installer.executors.storage import StorageStepExecutor
from arch_installer.executors.system import SystemStepExecutor
from arch_installer.executors.usb_boot import (
    UsbBootDriveStepExecutor,
    UsbBootSafeguardsStepExecutor,
)
from arch_installer.install_steps.questions import (
    Choice,
    ChooseMany,
    ChooseOne,
    EnterSecret,
    EnterText,
    MachineFacts,
    Switch,
)
from arch_installer.install_steps.wiring import (
    SettingLookup,
    StepSetting,
    StepWiring,
    always,
    when,
    when_equal,
)


class InstallStep(StrEnum):
    MIGRATION_STAGING = "Migration staging"
    USB_BOOT_DRIVE = "USB boot drive"
    STORAGE = "Storage"
    PACMAN_MIRRORS = "Pacman mirrors"
    PACKAGES = "Packages"
    MIGRATION_RESTORE = "Migration restore"
    SYSTEM = "System"
    DOCKER = "Docker"
    GPU_DRIVER = "GPU driver"
    KERNEL_IMAGES = "Kernel images"
    BOOTLOADER = "Bootloader"
    RECOVERY_SYSTEM = "Recovery system"
    SNAPPER = "Snapper"
    BOOTABLE_SNAPSHOTS = "Bootable snapshots"
    SNAPSHOT_NOTIFICATIONS = "Snapshot notifications"
    FIREWALL = "Firewall"
    USB_BOOT_SAFEGUARDS = "USB boot safeguards"


WIPE_CHOICES = (
    Choice(WipeMethod.QUICK, "Quick wipe (partition table only)"),
    Choice(WipeMethod.SECURE, "Secure wipe (random fill - slow)"),
    Choice(WipeMethod.DISCARD, "SSD discard (blkdiscard - fast)"),
    Choice(WipeMethod.SKIP, "Skip wipe (recovering partial install)"),
)
CPU_CHOICES = (Choice(CpuVendor.INTEL, "Intel"), Choice(CpuVendor.AMD, "AMD"))
GPU_CHOICES = (
    Choice(GpuVendor.AMD, "AMD (AMDGPU, open-source)"),
    Choice(GpuVendor.INTEL, "Intel (integrated graphics)"),
    Choice(GpuVendor.NVIDIA, "NVIDIA (proprietary/nouveau)"),
    Choice(GpuVendor.NONE, "None (VM or generic)"),
)
NVIDIA_DRIVER_CHOICES = (
    Choice(GpuDriver.NOUVEAU, "Nouveau - open-source, limited features"),
    Choice(GpuDriver.NVIDIA_OPEN, "NVIDIA Open - official open kernel modules, RTX 20+"),
    Choice(GpuDriver.NVIDIA_DKMS, "NVIDIA DKMS - proprietary, best compatibility"),
)
DESKTOP_CHOICES = (
    Choice(Desktop.GNOME, "GNOME (Wayland, modern)"),
    Choice(Desktop.KDE, "KDE Plasma (Wayland, customizable)"),
    Choice(Desktop.HYPRLAND, "Hyprland (Wayland tiling WM)"),
)
SWAP_PRESETS_MB = (4096, 8192, 16384, 32768, 65536)


def detected_disk_choices(inherited_value: Any, machine: MachineFacts) -> tuple[Choice, ...]:
    return tuple(
        Choice(disk.path, f"{disk.path}  {disk.model}  ({disk.size})") for disk in machine.disks()
    )


def _swap_label(size_mb: int) -> str:
    if size_mb % 1024 == 0:
        return f"{size_mb // 1024} GB"
    return f"{size_mb} MB"


# the inherited size is always offered, even when it is not one of the presets
def swap_size_choices(inherited_value: Any, machine: MachineFacts) -> tuple[Choice, ...]:
    sizes_mb = set(SWAP_PRESETS_MB)
    if isinstance(inherited_value, int) and inherited_value > 0:
        sizes_mb.add(inherited_value)
    return tuple(Choice(size_mb, _swap_label(size_mb)) for size_mb in sorted(sizes_mb))


def _mirrors_declared(value: SettingLookup) -> bool:
    return bool(value("system.mirrors.use_reflector") or value("system.mirrors.mirrors"))


def _uses_proprietary_nvidia_driver(value: SettingLookup) -> bool:
    return is_proprietary_nvidia(value("gpu.vendor"), value("gpu.driver"))


def _recovery_system_wanted(value: SettingLookup) -> bool:
    return bool(value("usb_boot.enabled") and value("usb_boot.recovery_system"))


def _snapshot_notifications_wanted(value: SettingLookup) -> bool:
    return bool(value("notifications.enabled") and value("snapper.enabled"))


INSTALL_STEPS: dict[InstallStep, StepWiring] = {
    InstallStep.MIGRATION_STAGING: StepWiring(
        config_sections=("migration",),
        settings=(
            StepSetting(
                "migration.enabled",
                EnvVariable.ENABLE_MIGRATION,
                Switch(
                    "Installation type",
                    on_label="Migration from existing Arch",
                    off_label="Fresh installation",
                ),
            ),
            StepSetting(
                "credentials.source_luks_password",
                EnvVariable.SOURCE_LUKS_PASSWORD,
                EnterSecret("Old disk password", "LUKS password of the existing install:", False),
                asked_when=when("migration.enabled"),
            ),
        ),
        enabled=when("migration.enabled"),
        executor=MigrationStagingStepExecutor,
    ),
    # the drive must exist before the storage step formats the LUKS header onto it
    InstallStep.USB_BOOT_DRIVE: StepWiring(
        config_sections=("usb_boot",),
        settings=(
            StepSetting(
                "usb_boot.enabled",
                EnvVariable.ENABLE_USB_BOOT,
                Switch(
                    "USB boot drive",
                    on_label="Yes - EFI partition and LUKS header on a USB drive",
                    off_label="No - boot from the internal disk",
                    description=(
                        "The internal disk keeps only ciphertext; "
                        "the system starts and unlocks only with the drive."
                    ),
                ),
            ),
            StepSetting(
                "usb_boot.device",
                EnvVariable.USB_BOOT_DEVICE,
                ChooseOne(
                    "USB drive",
                    detected_disk_choices,
                    description="Select the USB drive (ALL DATA ON IT WILL BE ERASED):",
                    typed_prompt="Enter the USB drive path (e.g. /dev/sdb):",
                ),
                asked_when=when("usb_boot.enabled"),
            ),
            StepSetting(
                "usb_boot.recovery_system",
                EnvVariable.ENABLE_RECOVERY_SYSTEM,
                Switch(
                    "Recovery system",
                    on_label="Yes - signed Arch live system on the drive",
                    off_label="No",
                    description="Boot the Arch ISO from the drive under Secure Boot to repair the system.",
                ),
                asked_when=when("usb_boot.enabled"),
            ),
            StepSetting(
                "usb_boot.iso_path",
                EnvVariable.ISO_PATH,
                EnterText("Recovery ISO", "Arch ISO file, or the live medium (e.g. /dev/sr0):"),
                asked_when=_recovery_system_wanted,
            ),
        ),
        enabled=when("usb_boot.enabled"),
        executor=UsbBootDriveStepExecutor,
    ),
    InstallStep.STORAGE: StepWiring(
        config_sections=("storage",),
        settings=(
            StepSetting(
                "storage.target_disk",
                EnvVariable.TARGET_DISK,
                ChooseOne(
                    "Disk",
                    detected_disk_choices,
                    description="Select target disk (ALL DATA WILL BE ERASED):",
                    typed_prompt="Enter disk path (e.g. /dev/sda):",
                ),
            ),
            StepSetting(
                "storage.wipe_method",
                EnvVariable.WIPE_METHOD,
                ChooseOne("Wipe method", WIPE_CHOICES),
            ),
            StepSetting("storage.swap.enabled", EnvVariable.ENABLE_SWAP, Switch("Swap file")),
            StepSetting(
                "storage.swap.size_mb",
                EnvVariable.SWAP_SIZE_MB,
                ChooseOne("Swap size", swap_size_choices),
                asked_when=when("storage.swap.enabled"),
            ),
            StepSetting(
                "storage.swap.hibernation",
                EnvVariable.ENABLE_HIBERNATION,
                Switch("Hibernation", description="Resume from the swapfile after power-off."),
                asked_when=when("storage.swap.enabled"),
            ),
            StepSetting(
                "credentials.luks_password",
                EnvVariable.LUKS_PASSWORD,
                EnterSecret("LUKS password", "LUKS encryption password:", True),
            ),
        ),
        enabled=always,
        executor=StorageStepExecutor,
    ),
    InstallStep.PACMAN_MIRRORS: StepWiring(
        config_sections=("system.mirrors",),
        settings=(),
        enabled=_mirrors_declared,
        executor=PacmanMirrorsStepExecutor,
    ),
    InstallStep.PACKAGES: StepWiring(
        config_sections=("packages", "gpu"),
        settings=(
            StepSetting(
                "system.cpu_vendor",
                EnvVariable.CPU_VENDOR,
                ChooseOne("CPU vendor", CPU_CHOICES),
            ),
            StepSetting(
                "gpu.vendor",
                EnvVariable.GPU_VENDOR,
                ChooseOne("GPU vendor", GPU_CHOICES),
            ),
            StepSetting(
                "gpu.driver",
                EnvVariable.GPU_DRIVER,
                ChooseOne("NVIDIA driver", NVIDIA_DRIVER_CHOICES),
                asked_when=when_equal("gpu.vendor", GpuVendor.NVIDIA),
            ),
            StepSetting(
                "packages.selected_desktops",
                EnvVariable.SELECTED_DESKTOPS,
                ChooseMany(
                    "Desktops",
                    DESKTOP_CHOICES,
                    description="Select desktop environments to install (Space to toggle):",
                ),
            ),
            StepSetting("boot.selected_kernels", EnvVariable.SELECTED_KERNELS, None),
        ),
        enabled=always,
        executor=PackagesStepExecutor,
    ),
    InstallStep.MIGRATION_RESTORE: StepWiring(
        config_sections=(),
        settings=(),
        enabled=when("migration.enabled"),
        executor=MigrationRestoreStepExecutor,
    ),
    InstallStep.SYSTEM: StepWiring(
        config_sections=("system",),
        settings=(
            StepSetting("system.hostname", None, EnterText("Hostname", "Hostname:")),
            StepSetting("system.user.name", None, EnterText("Username", "Username:")),
            StepSetting(
                "system.timezone", None, EnterText("Timezone", "Timezone (e.g. Europe/Helsinki):")
            ),
            StepSetting("system.locale.keymap", None, EnterText("Keymap", "Keymap:")),
            StepSetting(
                "credentials.user_password",
                EnvVariable.USER_PASSWORD,
                EnterSecret("User password", "User account password:", True),
            ),
        ),
        enabled=always,
        executor=SystemStepExecutor,
    ),
    InstallStep.DOCKER: StepWiring(
        config_sections=("docker",),
        settings=(StepSetting("docker.enabled", EnvVariable.ENABLE_DOCKER, Switch("Docker")),),
        enabled=when("docker.enabled"),
        executor=DockerStepExecutor,
    ),
    InstallStep.GPU_DRIVER: StepWiring(
        config_sections=(),
        settings=(),
        enabled=_uses_proprietary_nvidia_driver,
        executor=GpuDriverStepExecutor,
    ),
    InstallStep.KERNEL_IMAGES: StepWiring(
        config_sections=("boot",),
        settings=(),
        enabled=always,
        executor=KernelImagesStepExecutor,
    ),
    InstallStep.BOOTLOADER: StepWiring(
        config_sections=("boot.loader",),
        settings=(),
        enabled=always,
        executor=BootloaderStepExecutor,
    ),
    InstallStep.RECOVERY_SYSTEM: StepWiring(
        config_sections=(),
        settings=(),
        enabled=_recovery_system_wanted,
        executor=RecoverySystemStepExecutor,
    ),
    InstallStep.SNAPPER: StepWiring(
        config_sections=("snapper",),
        settings=(),
        enabled=when("snapper.enabled"),
        executor=SnapperStepExecutor,
    ),
    InstallStep.BOOTABLE_SNAPSHOTS: StepWiring(
        config_sections=(),
        settings=(
            StepSetting(
                "boot.enable_snapshot_boot",
                EnvVariable.ENABLE_SNAPSHOT_BOOT,
                Switch("Bootable snapshots"),
            ),
        ),
        enabled=when("boot.enable_snapshot_boot"),
        executor=BootableSnapshotsStepExecutor,
    ),
    InstallStep.SNAPSHOT_NOTIFICATIONS: StepWiring(
        config_sections=("notifications",),
        settings=(
            StepSetting(
                "notifications.enabled",
                EnvVariable.ENABLE_NOTIFICATIONS,
                Switch("Desktop notifications", description="Notify when a snapshot is taken."),
            ),
        ),
        enabled=_snapshot_notifications_wanted,
        executor=SnapshotNotificationsStepExecutor,
    ),
    InstallStep.FIREWALL: StepWiring(
        config_sections=("firewall",),
        settings=(
            StepSetting("firewall.enabled", EnvVariable.ENABLE_FIREWALL, Switch("Firewall (UFW)")),
        ),
        enabled=when("firewall.enabled"),
        executor=FirewallStepExecutor,
    ),
    # last, so the update guard never stands in the way of the installation itself
    InstallStep.USB_BOOT_SAFEGUARDS: StepWiring(
        config_sections=(),
        settings=(),
        enabled=when("usb_boot.enabled"),
        executor=UsbBootSafeguardsStepExecutor,
    ),
}

# make backup_to_usb is a tool of its own, not a step of an installation
USB_BACKUP_SETTINGS = (
    StepSetting("sync.backup_partition", EnvVariable.BACKUP_PARTITION, None),
    StepSetting("sync.backup_categories", EnvVariable.BACKUP_CATEGORIES, None),
)


def all_settings() -> tuple[StepSetting, ...]:
    install_settings = tuple(
        setting for wiring in INSTALL_STEPS.values() for setting in wiring.settings
    )
    return install_settings + USB_BACKUP_SETTINGS


def environment_variable_paths() -> dict[EnvVariable, str]:
    return {
        setting.environment_variable: setting.path
        for setting in all_settings()
        if setting.environment_variable is not None
    }


def variable_for_setting(setting_path: str) -> EnvVariable | None:
    return next(
        (
            setting.environment_variable
            for setting in all_settings()
            if setting.path == setting_path
        ),
        None,
    )
