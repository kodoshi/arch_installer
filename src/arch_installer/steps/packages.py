"""Package installation - base system, kernels, desktop environments."""

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.command import CommandRunner
from arch_installer.core.runtime_state import RuntimeConfig


class PackageInstaller:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
        enable_migration: bool = False,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._packages_config = config.packages
        self._enable_migration = enable_migration

    def install_packages(self) -> None:
        print(">>>>> Installing packages...")

        packages = self._collect_packages()
        print(f"    Total packages: {len(packages)}")

        self._create_vconsole_config()
        self._clear_pacman_locks()

        # backup secure boot keys before pacstrap if migration is enabled
        if self._enable_migration:
            self._backup_secure_boot_keys()

        self._run_pacstrap(packages)

        # restore secure boot keys after pacstrap if they were backed up
        if self._enable_migration:
            self._restore_secure_boot_keys()

        self._copy_pacman_config()
        self._generate_fstab()
        self._install_pacman_hooks()
        self._enable_services()

        print(">>>>> Package installation complete.")

    def _collect_packages(self) -> list[str]:
        packages: list[str] = []

        profile = self._state.package_profile
        print(f"    Profile: {profile}")

        packages.extend(self._packages_config.base)

        packages = self._filter_microcode(packages)
        packages = self._filter_kernels(packages)
        packages.extend(self._get_desktop_packages())
        packages.extend(self._get_gpu_packages())

        print(
            f"    base={len(list(self._packages_config.base))}, "
            f"desktop={len(self._get_desktop_packages())}, "
            f"gpu={len(self._get_gpu_packages())}"
        )

        return packages

    def _filter_microcode(self, packages: list[str]) -> list[str]:
        cpu_vendor = self._state.cpu_vendor
        if not cpu_vendor:
            return packages

        print(f"    CPU vendor: {cpu_vendor}")
        filtered = []
        for pkg in packages:
            if pkg == "intel-ucode" and cpu_vendor != "intel":
                continue
            if pkg == "amd-ucode" and cpu_vendor != "amd":
                continue
            filtered.append(pkg)
        return filtered

    def _filter_kernels(self, packages: list[str]) -> list[str]:
        selected = self._state.selected_kernels
        if not selected:
            return packages

        print(f"    Selected kernels: {', '.join(selected)}")
        filtered = []
        kernel_packages = {
            "linux": ["linux", "linux-headers"],
            "linux-hardened": ["linux-hardened", "linux-hardened-headers"],
            "linux-lts": ["linux-lts", "linux-lts-headers"],
        }

        for pkg in packages:
            is_kernel_pkg = False
            for kernel, kernel_pkgs in kernel_packages.items():
                if pkg in kernel_pkgs:
                    is_kernel_pkg = True
                    if kernel in selected:
                        filtered.append(pkg)
                    break

            if not is_kernel_pkg:
                filtered.append(pkg)

        return filtered

    def _get_desktop_packages(self) -> list[str]:
        selected = self._state.selected_desktops
        if not selected:
            return []

        print(f"    Selected desktops: {', '.join(selected)}")
        packages: list[str] = []

        desktops = self._packages_config.desktops
        for desktop in selected:
            if desktop == "gnome":
                packages.extend(desktops.gnome)
            elif desktop == "kde":
                packages.extend(desktops.kde)
            elif desktop == "hyprland":
                packages.extend(desktops.hyprland)

        if packages:
            packages.extend(self._packages_config.display_manager)

        return packages

    def _get_gpu_packages(self) -> list[str]:
        gpu_vendor = self._state.gpu_vendor
        gpu_driver = self._state.gpu_driver

        if gpu_vendor == "none" or not gpu_vendor:
            return []

        print(f"    GPU vendor: {gpu_vendor}" + (f" ({gpu_driver})" if gpu_driver else ""))

        gpu_config = self._config.gpu
        drivers = gpu_config.drivers

        if gpu_vendor == "amd":
            return list(drivers.amd)
        elif gpu_vendor == "intel":
            return list(drivers.intel)
        elif gpu_vendor == "nvidia":
            if gpu_driver == "nouveau":
                return list(drivers.nouveau)
            elif gpu_driver == "nvidia-open":
                return list(drivers.nvidia_open)
            else:
                return list(drivers.nvidia_dkms)

        return []

    def _create_vconsole_config(self) -> None:
        vconsole_path = f"{self._state.target_root}/etc/vconsole.conf"
        keymap = self._config.system.locale.keymap

        self._runner.run(f"mkdir -p {self._state.target_root}/etc")

        result = self._runner.run(f"test -f {vconsole_path}", raise_on_nonzero_exit=False)
        if result.success:
            return

        print(">>>>> Creating /etc/vconsole.conf (pre-pacstrap)...")
        self._runner.run(f'echo "KEYMAP={keymap}" > {vconsole_path}')

    def _clear_pacman_locks(self) -> None:
        self._runner.run("rm -f /var/lib/pacman/db.lck", raise_on_nonzero_exit=False)
        self._runner.run(
            f"mkdir -p {self._state.target_root}/var/lib/pacman", raise_on_nonzero_exit=False
        )
        self._runner.run(
            f"rm -f {self._state.target_root}/var/lib/pacman/db.lck", raise_on_nonzero_exit=False
        )

    def _run_pacstrap(self, packages: list[str]) -> None:
        if not packages:
            print("    No packages to install.")
            return

        # check if pacstrap has already run (system exists)
        os_release_check = self._runner.run(
            f"test -f {self._state.target_root}/etc/os-release",
            raise_on_nonzero_exit=False,
        )
        if os_release_check.success and not self._enable_migration:
            print("    System already installed, updating packages instead of pacstrap...")
            pkg_list = " ".join(packages)
            self._runner.run_as_chroot(
                f"pacman -Syu --noconfirm --needed {pkg_list}",
                raise_on_nonzero_exit=False,
            )
            return

        pkg_list = " ".join(packages)
        # use --overwrite in migration mode to handle existing files
        overwrite_flag = " --overwrite '*'" if self._enable_migration else ""
        self._runner.run(f"pacstrap -K /mnt --noconfirm{overwrite_flag} {pkg_list}")

    def _backup_secure_boot_keys(self) -> None:
        # sbctl key locations: new default is /var/lib/sbctl, legacy is /usr/share/secureboot
        new_sbctl_dir = f"{self._state.target_root}/var/lib/sbctl"
        legacy_sbctl_dir = f"{self._state.target_root}/usr/share/secureboot"
        backup_dir = "/tmp/secureboot-keys-backup"

        # check new path first (sbctl >= 0.x default)
        result = self._runner.run(f"test -d {new_sbctl_dir}/keys", raise_on_nonzero_exit=False)
        if result.success:
            source_dir = new_sbctl_dir
        else:
            # check legacy path (/usr/share/secureboot)
            result = self._runner.run(
                f"test -d {legacy_sbctl_dir}/keys", raise_on_nonzero_exit=False
            )
            if result.success:
                source_dir = legacy_sbctl_dir
            else:
                print("    No existing secure boot keys to backup.")
                return

        print(f">>>>> Backing up existing secure boot keys from {source_dir}...")
        self._runner.run(f"rm -rf {backup_dir}")
        self._runner.run(f"mkdir -p {backup_dir}")
        self._runner.run(f"cp -a {source_dir} {backup_dir}/sbctl")
        print(f"    Backed up {source_dir} to {backup_dir}/sbctl")

    def _restore_secure_boot_keys(self) -> None:
        backup_dir = "/tmp/secureboot-keys-backup/sbctl"

        result = self._runner.run(f"test -d {backup_dir}/keys", raise_on_nonzero_exit=False)
        if not result.success:
            print("    No backed up secure boot keys to restore.")
            return

        # always restore to new default path (/var/lib/sbctl)
        target_dir = f"{self._state.target_root}/var/lib/sbctl"

        print(f">>>>> Restoring secure boot keys to {target_dir}...")
        self._runner.run(f"mkdir -p {target_dir}")
        self._runner.run(f"cp -a {backup_dir}/* {target_dir}/")
        self._runner.run("rm -rf /tmp/secureboot-keys-backup")
        print(f"    Restored secure boot keys to {target_dir}")

    def _copy_pacman_config(self) -> None:
        pacman_conf = f"{self._state.target_root}/etc/pacman.conf"
        result = self._runner.run(f"test -f {pacman_conf}", raise_on_nonzero_exit=False)
        if not result.success:
            print(">>>>> Copying pacman.conf...")
            self._runner.run(f"cp /etc/pacman.conf {pacman_conf}")

    def _generate_fstab(self) -> None:
        fstab_path = f"{self._state.target_root}/etc/fstab"

        result = self._runner.run(f"grep -q '^[^#]' {fstab_path}", raise_on_nonzero_exit=False)
        if result.success:
            print("    fstab already has entries.")
            return

        print(">>>>> Generating fstab...")
        self._runner.run(f"genfstab -U /mnt >> {fstab_path}")

    def _install_pacman_hooks(self) -> None:
        print(">>>>> Installing pacman hooks...")

        hooks_dir = f"{self._state.target_root}/etc/pacman.d/hooks"
        scripts_dir = f"{self._state.target_root}/usr/local/bin"

        self._runner.run(f"mkdir -p {hooks_dir}")
        self._runner.run(f"mkdir -p {scripts_dir}")

        if self._state.enable_snapshot_boot:
            print("    Installing bootable snapshot hooks...")
            self._install_snapshot_hooks(hooks_dir, scripts_dir)
        else:
            print("    Bootable snapshots disabled, skipping hooks.")

    def _install_snapshot_hooks(self, hooks_dir: str, scripts_dir: str) -> None:
        hook_src = "config/pacman/hooks/95-snapshot-uki-refresh.hook"
        result = self._runner.run(f"test -f {hook_src}", raise_on_nonzero_exit=False)
        if result.success:
            self._runner.run(f"cp {hook_src} {hooks_dir}/")

        script_src = "config/pacman/scripts/refresh-snapshot-ukis"
        result = self._runner.run(f"test -f {script_src}", raise_on_nonzero_exit=False)
        if result.success:
            self._runner.run(f"cp {script_src} {scripts_dir}/")
            self._runner.run(f"chmod +x {scripts_dir}/refresh-snapshot-ukis")

        manage_src = "scripts/manage_snapshot_entries.sh"
        result = self._runner.run(f"test -f {manage_src}", raise_on_nonzero_exit=False)
        if result.success:
            self._runner.run(f"cp {manage_src} {scripts_dir}/manage-snapshot-ukis")
            self._runner.run(f"chmod +x {scripts_dir}/manage-snapshot-ukis")

    def _enable_services(self) -> None:
        print(">>>>> Enabling services...")

        self._runner.run_as_chroot("systemctl enable NetworkManager.service")

        # Enable SSH if openssh is installed (needed for testing and remote access)
        result = self._runner.run_as_chroot("pacman -Q openssh", raise_on_nonzero_exit=False)
        if result.success:
            self._runner.run_as_chroot("systemctl enable sshd.service")

        if self._state.selected_desktops:
            result = self._runner.run_as_chroot("pacman -Q sddm", raise_on_nonzero_exit=False)
            if result.success:
                self._runner.run_as_chroot(
                    "systemctl enable sddm.service", raise_on_nonzero_exit=False
                )
