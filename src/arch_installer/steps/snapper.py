"""Snapper configuration for BTRFS snapshots."""

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.command import CommandRunner
from arch_installer.core.runtime_state import RuntimeConfig
from arch_installer.templates.snapper_btrfs import (
    SNAP_PAC_ROOT_CONFIG,
    SNAPPER_BOOT_ENTRIES_REFRESH_SERVICE,
    SNAPPER_BOOT_ENTRIES_WATCH_PATH,
    SNAPPER_CONFIGS_LIST,
    SNAPPER_NOTIFY_PATH,
    SNAPPER_NOTIFY_SCRIPT,
    SNAPPER_NOTIFY_SERVICE,
    snapper_volume_config,
)


class SnapperSetup:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._snapper_config = config.snapper

    def configure_snapper(self) -> None:
        if not self._snapper_config.enabled:
            print(">>>>> Snapper disabled in config, skipping.")
            return

        print(">>>>> Configuring snapper...")

        allow_groups = ",".join(self._snapper_config.allow_groups)
        print(f"    Allow groups: {allow_groups}")

        self._ensure_snapper_installed()
        self._create_config_directories()

        mount_opts = self._config.storage.btrfs.mount_options

        if self._snapper_config.root:
            self._configure_root_snapshots(allow_groups, mount_opts)

        if self._snapper_config.home:
            self._configure_home_snapshots(allow_groups, mount_opts)

        self._register_configs()
        self._enable_timers()

        if self._snapper_config.snap_pac.enabled:
            self._configure_snap_pac()

        if self._state.enable_snapshot_boot:
            self._setup_bootable_snapshots()

        if self._snapper_config.notifications:
            self._setup_notifications()

        print(">>>>> Snapper configuration complete.")

    def _ensure_snapper_installed(self) -> None:
        result = self._runner.run_as_chroot("pacman -Q snapper", raise_on_nonzero_exit=False)
        if not result.success:
            print("    Installing snapper and snap-pac...")
            self._runner.run_as_chroot("pacman -S --noconfirm snapper snap-pac")

    def _create_config_directories(self) -> None:
        self._runner.run("mkdir -p /mnt/etc/snapper/configs")
        self._runner.run("mkdir -p /mnt/etc/conf.d")

    def _configure_root_snapshots(self, allow_groups: str, mount_opts: str) -> None:
        root_config = "/mnt/etc/snapper/configs/root"
        retention = self._snapper_config.root.retention if self._snapper_config.root else None

        if not retention:
            return

        result = self._runner.run(f"test -f {root_config}", raise_on_nonzero_exit=False)
        if not result.success:
            print("    Creating snapper config for root...")
            self._setup_snapshots_directory("/.snapshots", "@snapshots", mount_opts)
        else:
            print("    Root config already exists.")

        print("    Configuring root snapshot settings...")

        config_content = snapper_volume_config(
            subvolume="/",
            allow_groups=allow_groups,
            number_limit=10,
            number_limit_important=5,
            hourly=retention.hourly,
            daily=retention.daily,
            weekly=retention.weekly,
            monthly=retention.monthly,
            yearly=retention.yearly,
        )

        self._runner.run(f"cat > {root_config} << 'EOF'\n{config_content}EOF")

    def _configure_home_snapshots(self, allow_groups: str, mount_opts: str) -> None:
        home_config = "/mnt/etc/snapper/configs/home"
        retention = self._snapper_config.home.retention if self._snapper_config.home else None

        if not retention:
            return

        result = self._runner.run(f"test -f {home_config}", raise_on_nonzero_exit=False)
        if not result.success:
            print("    Creating snapper config for home...")
            self._setup_snapshots_directory("/home/.snapshots", "@home-snapshots", mount_opts)
        else:
            print("    Home config already exists.")

        print("    Configuring home snapshot settings...")

        config_content = snapper_volume_config(
            subvolume="/home",
            allow_groups=allow_groups,
            number_limit=5,
            number_limit_important=3,
            hourly=retention.hourly,
            daily=retention.daily,
            weekly=retention.weekly,
            monthly=retention.monthly,
            yearly=retention.yearly,
        )

        self._runner.run(f"cat > {home_config} << 'EOF'\n{config_content}EOF")

    def _setup_snapshots_directory(
        self, snapshots_path: str, subvol_name: str, mount_opts: str
    ) -> None:
        full_path = f"/mnt{snapshots_path}"

        result = self._runner.run(f"mountpoint -q {full_path}", raise_on_nonzero_exit=False)
        if result.success:
            self._runner.run(f"umount {full_path}")

        result = self._runner.run(f"test -d {full_path}", raise_on_nonzero_exit=False)
        if result.success:
            result = self._runner.run(
                f"btrfs subvolume show {full_path}", raise_on_nonzero_exit=False
            )
            if result.success:
                self._runner.run(
                    f"btrfs subvolume delete {full_path}", raise_on_nonzero_exit=False
                )
            else:
                self._runner.run(f"rmdir {full_path}", raise_on_nonzero_exit=False)

        self._runner.run(f"mkdir -p {full_path}")
        self._runner.run(
            f"mount -o subvol={subvol_name},{mount_opts} /dev/mapper/cryptroot {full_path}"
        )

        self._runner.run(f"chmod 750 {full_path}")

    def _register_configs(self) -> None:
        print("    Registering snapper configs...")
        self._runner.run(f"cat > /mnt/etc/conf.d/snapper << 'EOF'\n{SNAPPER_CONFIGS_LIST}EOF")

    def _enable_timers(self) -> None:
        print("    Enabling snapper timers...")
        self._runner.run_as_chroot("systemctl enable snapper-timeline.timer")
        self._runner.run_as_chroot("systemctl enable snapper-cleanup.timer")

    def _configure_snap_pac(self) -> None:
        print("    Configuring snap-pac hooks...")
        self._runner.run("mkdir -p /mnt/etc/snap-pac.d")
        self._runner.run(
            f"cat > /mnt/etc/snap-pac.d/root.conf << 'EOF'\n{SNAP_PAC_ROOT_CONFIG}EOF"
        )

    def _setup_bootable_snapshots(self) -> None:
        print("    Installing bootable snapshot support...")

        scripts_dir = "/mnt/etc/snapper/scripts"
        self._runner.run(f"mkdir -p {scripts_dir}")

        script_src = "scripts/manage_snapshot_entries.sh"
        result = self._runner.run(f"test -f {script_src}", raise_on_nonzero_exit=False)
        if result.success:
            self._runner.run(f"cp {script_src} {scripts_dir}/refresh-boot-entries.sh")
            self._runner.run(f"chmod +x {scripts_dir}/refresh-boot-entries.sh")

        self._runner.run(
            f"cat > /mnt/etc/systemd/system/snapper-boot-entries.service << 'EOF'\n{SNAPPER_BOOT_ENTRIES_REFRESH_SERVICE}EOF"
        )

        self._runner.run(
            f"cat > /mnt/etc/systemd/system/snapper-boot-entries.path << 'EOF'\n{SNAPPER_BOOT_ENTRIES_WATCH_PATH}EOF"
        )

        self._runner.run_as_chroot("systemctl enable snapper-boot-entries.path")
        print("    Bootable snapshots enabled.")

    def _setup_notifications(self) -> None:
        print("    Installing snapshot notification support...")

        # install the notification script
        self._runner.run("mkdir -p /mnt/usr/local/bin")
        self._runner.run(
            f"cat > /mnt/usr/local/bin/snapper-notify << 'EOF'\n{SNAPPER_NOTIFY_SCRIPT}EOF"
        )
        self._runner.run("chmod +x /mnt/usr/local/bin/snapper-notify")

        # install systemd service and path units
        self._runner.run(
            f"cat > /mnt/etc/systemd/system/snapper-notify.service << 'EOF'\n{SNAPPER_NOTIFY_SERVICE}EOF"
        )
        self._runner.run(
            f"cat > /mnt/etc/systemd/system/snapper-notify.path << 'EOF'\n{SNAPPER_NOTIFY_PATH}EOF"
        )

        self._runner.run_as_chroot("systemctl enable snapper-notify.path")
        print("    Snapshot notifications enabled.")
