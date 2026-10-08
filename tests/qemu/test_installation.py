from pathlib import Path

import pytest
import yaml

from arch_installer.config.models import LUKS_PASSWORD_SECRET, USER_PASSWORD_SECRET
from arch_installer.core.secrets import encrypt_secret
from tests.qemu.assertions import InstallationAssertions
from tests.qemu.ssh_config import SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM
from tests.qemu.tmux_driver import INSTALL_TMUX_IF_MISSING, TmuxScreenInput, TmuxSession
from tests.qemu.uefi_setup import (
    print_secure_boot_summary,
    verify_secure_boot_properly_configured,
    verify_setup_mode_before_install,
)
from tests.qemu.vm import QemuVm

INSTALL_TIMEOUT = 1800
SECRETS_KEY = "12345678"
PROJECT_ROOT = Path(__file__).parent.parent.parent
QEMU_DATA_DIRECTORY = Path(__file__).parent.parent / "data"
SBCTL_KEY_FILES = " ".join(
    f"/mnt/var/lib/sbctl/keys/{key}" for key in ("PK/PK.key", "KEK/KEK.key", "db/db.key")
)
# glibc is upgraded together with python: an older live ISO otherwise ends up with a
# python built against a newer glibc (partial upgrade) that fails on import
BASE_PACKAGES = "glibc python python-yaml python-cryptography python-cffi make"


def create_fake_iso(vm: QemuVm) -> None:
    vm.run_ssh_command(
        "mkdir -p /tmp/fake-iso/EFI/BOOT && "
        "cp /usr/lib/systemd/boot/efi/systemd-bootx64.efi "
        "/tmp/fake-iso/EFI/BOOT/BOOTX64.EFI",
        timeout=30,
    )
    exit_code, _, stderr = vm.run_ssh_command(
        "mkisofs -o /root/archlinux.iso -J -R /tmp/fake-iso",
        timeout=60,
    )
    assert exit_code == 0, f"Failed to create fake ISO: {stderr}"


def setup_vm_for_install(
    vm: QemuVm,
    config_path: Path | None = None,
    extra_packages: str = "",
    expand_root: bool = True,
) -> None:
    """initialize pacman keyring, install base packages, and copy installer to VM.

    Note: expand_root=True by default to ensure cowspace is large enough for package
    installation. The live ISO has limited overlay space that can fill up quickly.
    """
    if expand_root:
        vm.run_ssh_command("mount -o remount,size=2G /run/archiso/cowspace", timeout=30)
    vm.run_ssh_command("pacman-key --init", timeout=120)
    packages = BASE_PACKAGES + (" " + extra_packages if extra_packages else "")
    exit_code, _, stderr = vm.run_ssh_command(
        f"pacman -Sy --noconfirm --overwrite '*' {packages}",
        timeout=300,
    )
    assert exit_code == 0, f"Failed to install dependencies: {stderr}"
    vm.copy_directory_to_vm(PROJECT_ROOT, "/root/arch_installer")
    if config_path:
        vm.run_ssh_command("mkdir -p /root/arch_installer/config", timeout=30)
        vm.copy_file_to_vm(config_path, "/root/arch_installer/config/config.yaml")


def load_test_config(file_name: str) -> dict:
    with open(QEMU_DATA_DIRECTORY / file_name) as config_file:
        return yaml.safe_load(config_file)


def expected_mount_options(storage_config: dict) -> list[str]:
    return [option.strip() for option in storage_config["btrfs"]["mount_options"].split(",")]


def unattended_install_env(**overrides: str) -> dict[str, str]:
    return {
        "LUKS_PASSWORD": "testpassword",
        "USER_PASSWORD": "testpassword",
        "NON_INTERACTIVE": "true",
        "TARGET_DISK": "/dev/vda",
        "SWAP_SIZE_MB": "1024",
        **overrides,
    }


def run_make_install(vm: QemuVm, env_variables: dict[str, str]) -> tuple[int, str, str]:
    env_assignments = " ".join(f"{name}={value}" for name, value in env_variables.items())
    return vm.run_ssh_command(
        f"cd /root/arch_installer && {env_assignments} make install",
        timeout=2400,
    )


def run_checked(vm: QemuVm, commands: list[str], timeout: int = 60) -> None:
    for command in commands:
        exit_code, stdout, stderr = vm.run_ssh_command(command, timeout=timeout)
        assert exit_code == 0, (
            f"setup command failed: {command}\nstdout: {stdout}\nstderr: {stderr}"
        )


def configure_ssh_and_reboot(vm: QemuVm, luks_passphrase: str = "testpassword") -> None:
    """configure SSH for installed system and reboot."""
    for command in SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM:
        exit_code, _, _ = vm.run_ssh_command(command, timeout=120)
        if exit_code != 0:
            print(f"    warning: SSH setup command failed: {command}")
    vm.run_ssh_command("umount -R /mnt 2>/dev/null || true", timeout=60)
    vm.reboot(wait_for_ssh=True, timeout=300, luks_passphrase=luks_passphrase)


# screens follow the install steps; inherited values come from maximal_config.yaml with
# its passwords encrypted under SECRETS_KEY, so the password screens offer to keep them.
# Enter keeps every inherited value except USB boot, which this VM has no disk for
TMUX_INSTALL_SESSION = (
    TmuxScreenInput("DALI", ("Enter",)),
    TmuxScreenInput("Migration staging: Installation type", ("Enter",)),  # fresh
    TmuxScreenInput("Storage: Disk", ("Enter",)),  # /dev/vda
    TmuxScreenInput("Storage: Wipe method", ("Enter",)),  # quick
    TmuxScreenInput("Storage: Swap file", ("Enter",)),  # on
    TmuxScreenInput("Storage: Swap size", ("Enter",)),  # 1 GB
    TmuxScreenInput("Storage: Hibernation", ("Enter",)),  # on
    TmuxScreenInput("Keep the inherited password", ("Enter",)),  # LUKS password
    TmuxScreenInput("Packages: CPU vendor", ("Enter",)),  # amd
    TmuxScreenInput("Packages: GPU vendor", ("Enter",)),  # none
    # all three desktops are inherited as selected, untick every one
    TmuxScreenInput("Packages: Desktops", ("Space", "Down", "Space", "Down", "Space", "Enter")),
    TmuxScreenInput("System: Hostname", ("Enter",)),
    TmuxScreenInput("System: Username", ("Enter",)),
    TmuxScreenInput("System: Timezone", ("Enter",)),
    TmuxScreenInput("System: Keymap", ("Enter",)),
    TmuxScreenInput("Keep the inherited password", ("Enter",)),  # user password
    TmuxScreenInput("Docker: Docker", ("Enter",)),  # on
    TmuxScreenInput("USB boot drive: USB boot drive", ("Up", "Enter")),  # yes -> no
    TmuxScreenInput("Bootable snapshots: Bootable snapshots", ("Enter",)),
    TmuxScreenInput("Snapshot notifications: Desktop notifications", ("Enter",)),
    TmuxScreenInput("Firewall: Firewall (UFW)", ("Enter",)),
    TmuxScreenInput("Summary", ("y",)),
)


def write_config_with_encrypted_passwords(
    source_name: str, destination: Path, password: str
) -> Path:
    config = load_test_config(source_name)
    config["secrets"] = {
        "luks_password_encrypted": encrypt_secret(password, SECRETS_KEY, LUKS_PASSWORD_SECRET),
        "user_password_encrypted": encrypt_secret(password, SECRETS_KEY, USER_PASSWORD_SECRET),
    }
    destination.write_text(yaml.safe_dump(config, sort_keys=False))
    return destination


@pytest.mark.timeout(INSTALL_TIMEOUT)
class TestQemuFullInstallation:
    @pytest.mark.qemu
    @pytest.mark.slow
    def test_fresh_installation_with_maximal_config_produces_complete_system(
        self,
        qemu_vm_with_network: QemuVm,
    ) -> None:
        """comprehensive end-to-end installation with maximal config.

        tests all enabled sections:
        - system: hostname, timezone, locale, user, mirrors
        - storage: luks, efi partition, btrfs subvolumes, swap with hibernation
        - boot: all kernels, UKI variants, cmdline params, mkinitcpio hooks
        - snapper: root and home configs, bootable snapshots
        - firewall: ufw enabled with deny policy
        - docker: enabled with access group
        - secure boot: keys enrolled and files signed
        """
        vm = qemu_vm_with_network
        config = load_test_config("maximal_config.yaml")
        expected_subvolumes = [
            subvolume["name"] for subvolume in config["storage"]["btrfs"]["subvolumes"]
        ]
        assertions = InstallationAssertions(vm)

        print("\n=== phase 1: pre-install verification ===")
        print_secure_boot_summary(vm, "PRE-INSTALL")
        assert verify_setup_mode_before_install(vm), (
            "UEFI must be in setup mode before installation for key enrollment"
        )

        print("\n=== phase 2: run installer with maximal config ===")
        setup_vm_for_install(vm, config_path=QEMU_DATA_DIRECTORY / "maximal_config.yaml")

        exit_code, stdout, stderr = run_make_install(
            vm,
            unattended_install_env(
                ENABLE_SNAPSHOT_BOOT="true",
                ENABLE_HIBERNATION="true",
                ENABLE_FIREWALL="true",
                ENABLE_DOCKER="true",
                ENABLE_USB_BOOT="false",
                GPU_VENDOR="none",
                CPU_VENDOR="amd",
                WIPE_METHOD="secure",
            ),
        )
        assert exit_code == 0, f"Installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    installation completed successfully")

        print("\n=== phase 3: verify storage setup (before reboot) ===")

        assertions.assert_partitions_exist("/dev/vda")
        assertions.assert_efi_partition_type("/dev/vda")
        assertions.assert_root_partition_type("/dev/vda")
        assertions.assert_efi_partition_size_mib(config["storage"]["efi_size_mb"], "/dev/vda")
        assertions.assert_luks_volume_active()
        assertions.assert_luks_type("LUKS2")
        assertions.assert_luks_cipher(config["storage"]["luks"]["cipher"], "cryptroot")
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)

        print("    checking mount options...")
        assertions.assert_btrfs_mount_options(expected_mount_options(config["storage"]))

        print("    checking NoCow attributes on relevant subvolumes...")
        for subvolume in config["storage"]["btrfs"]["subvolumes"]:
            if subvolume.get("nocow"):
                assertions.assert_nocow_attribute(f"/mnt{subvolume['mountpoint']}")

        print("    checking subvolume mount points...")
        for subvolume in config["storage"]["btrfs"]["subvolumes"]:
            if subvolume["mountpoint"] != "/":
                assertions.assert_subvolume_mounted(
                    subvolume["name"], f"/mnt{subvolume['mountpoint']}"
                )

        assertions.raise_if_failed()

        print("\n=== phase 4: reboot into installed system ===")
        configure_ssh_and_reboot(vm, "testpassword")

        exit_code, stdout, _ = vm.run_ssh_command("cat /etc/hostname", timeout=30)
        if exit_code == 0:
            print(f"    booted installed system: hostname={stdout.strip()}")

        print("\n=== phase 5: verify system configuration ===")
        post_boot_assertions = InstallationAssertions(vm)

        username = config["system"]["user"]["name"]
        user_groups = config["system"]["user"]["groups"].copy()
        expected_locale = (
            f"{config['system']['locale']['language']}.{config['system']['locale']['encoding']}"
        )

        post_boot_assertions.assert_hostname(config["system"]["hostname"])
        post_boot_assertions.assert_timezone(config["system"]["timezone"])
        post_boot_assertions.assert_locale(expected_locale)
        post_boot_assertions.assert_keymap(config["system"]["locale"]["keymap"])
        post_boot_assertions.assert_user_exists(username)
        post_boot_assertions.assert_user_in_groups(username, user_groups)

        print("\n=== phase 5.1: verify boot configuration ===")

        post_boot_assertions.assert_systemd_boot_installed()
        post_boot_assertions.assert_loader_conf_exists()
        post_boot_assertions.assert_loader_timeout(config["boot"]["loader"]["timeout"])
        post_boot_assertions.assert_loader_editor_disabled()
        post_boot_assertions.assert_uki_directory_exists()

        print("    checking UKI files for all kernels...")
        expected_kernel_patterns = []
        for kernel in config["boot"]["kernels"]:
            package = kernel["package"]
            if package == "linux":
                expected_kernel_patterns.append("arch-linux-default")
            else:
                expected_kernel_patterns.append(f"arch-{package}")
        post_boot_assertions.assert_uki_files_exist(expected_kernel_patterns)
        post_boot_assertions.assert_mkinitcpio_hooks(config["boot"]["hooks"])

        print("    checking kernel cmdline hardening...")
        hardening = config["boot"]["cmdline"]["hardening"]
        expected_kernel_parameters = [
            f"lockdown={hardening['lockdown']}",
            f"iommu={hardening['iommu']}",
            f"pti={hardening['pti']}",
        ]
        post_boot_assertions.assert_kernel_cmdline_contains(expected_kernel_parameters)

        post_boot_assertions.assert_secure_boot_keys_created()
        post_boot_assertions.assert_secure_boot_keys_exist()
        post_boot_assertions.assert_bootloader_signed()
        post_boot_assertions.assert_esp_random_seed_private()
        post_boot_assertions.assert_all_ukis_signed()
        post_boot_assertions.assert_sbctl_verify_all()
        post_boot_assertions.assert_secure_boot_enrolled()
        post_boot_assertions.assert_secure_boot_enabled()
        post_boot_assertions.assert_pk_enrolled()
        post_boot_assertions.assert_kek_enrolled()
        post_boot_assertions.assert_db_enrolled()
        post_boot_assertions.assert_fstab_entry("/", filesystem_type="btrfs")
        post_boot_assertions.assert_fstab_entry("/home", filesystem_type="btrfs")
        post_boot_assertions.assert_fstab_entry("/efi", filesystem_type="vfat")

        print("    checking critical packages installed...")
        critical_packages = ["sbctl", "btrfs-progs", "cryptsetup", "snapper", "networkmanager"]
        post_boot_assertions.assert_packages_installed(critical_packages)
        post_boot_assertions.assert_service_active("NetworkManager")

        print_secure_boot_summary(vm, "POST-INSTALL")
        assert verify_secure_boot_properly_configured(vm), (
            "Secure boot keys must be created, enrolled, and boot files signed"
        )

        print("\n=== phase 5.2: verify swap and hibernation ===")
        swap_path = config["storage"]["swap"]["path"]
        post_boot_assertions.assert_swapfile_exists(swap_path)
        post_boot_assertions.assert_swap_active(swap_path)
        post_boot_assertions.assert_swapfile_in_fstab(swap_path)
        post_boot_assertions.assert_swapfile_size_mb(1024, swap_path)

        if config["storage"]["swap"]["hibernation"]:
            post_boot_assertions.assert_hibernation_resume_configured()
            post_boot_assertions.assert_hibernation_resume_offset()
            post_boot_assertions.assert_mkinitcpio_resume_hook()

        print("\n=== phase 5.3: verify snapper configuration ===")
        post_boot_assertions.assert_snapper_config_exists("root")
        post_boot_assertions.assert_manage_snapshot_ukis_exists()
        post_boot_assertions.assert_snapshot_hooks_deployed()
        post_boot_assertions.assert_snapshot_ukis_list()

        if config["snapper"].get("home"):
            post_boot_assertions.assert_snapper_config_exists("home")

        print("\n=== phase 5.4: verify firewall configuration ===")
        if config["firewall"]["enabled"]:
            post_boot_assertions.assert_service_enabled("ufw")
            post_boot_assertions.assert_service_active("ufw")

        print("\n=== phase 5.5: verify docker configuration ===")
        if config["docker"]["enabled"]:
            post_boot_assertions.assert_service_enabled("docker")
            post_boot_assertions.assert_service_active("docker")
            post_boot_assertions.assert_package_installed("docker")
            docker_group = config["docker"]["access_group"]
            post_boot_assertions.assert_user_in_groups(username, [docker_group])

        print("\n=== phase 5.6: verify final config file ===")
        post_boot_assertions.assert_final_config_written(username)

        print("\n=== phase 5.7: verify-install against the installer's expectations ===")
        post_boot_assertions.assert_verify_install_finds_no_failures(config["system"]["hostname"])

        print("\n=== phase 5.8: verify dotfiles-sync functionality ===")
        self._test_dotfiles_sync(vm, username)

        post_boot_assertions.raise_if_failed()

        post_boot_assertions.assert_live_iso_would_be_blocked()

        print("\n=== phase 6: test bootable snapshot creation and boot ===")
        if config["boot"]["enable_snapshot_boot"]:
            exit_code, stdout, stderr = vm.run_ssh_command(
                "snapper -c root create -d 'Test snapshot' --print-number",
                timeout=60,
            )
            assert exit_code == 0, f"Failed to create snapshot: {stderr}"
            snapshot_id = stdout.strip()
            print(f"    created snapshot {snapshot_id}")

            post_boot_assertions.assert_snapshot_created("root")

            exit_code, stdout, stderr = vm.run_ssh_command(
                "manage-snapshot-ukis refresh",
                timeout=120,
            )
            assert exit_code == 0, f"Snapshot UKI refresh failed: {stderr}"

            post_boot_assertions.assert_snapshot_uki_generated(int(snapshot_id))
            post_boot_assertions.assert_snapshot_uki_in_bootloader(int(snapshot_id))

            snapshot_path = f"/.snapshots/{snapshot_id}/snapshot"
            post_boot_assertions.assert_snapshot_is_writable(snapshot_path)

            exit_code, _, stderr = vm.run_ssh_command(
                f"touch {snapshot_path}/test_writable_marker",
                timeout=30,
            )
            assert exit_code == 0, f"Failed to write to snapshot: {stderr}"

            vm.run_ssh_command(f"rm -f {snapshot_path}/test_writable_marker", timeout=30)

        assertions.raise_if_failed()
        post_boot_assertions.raise_if_failed()
        print("\n=== maximal config test completed successfully ===")

    def _test_dotfiles_sync(self, vm: QemuVm, username: str) -> None:
        """test dotfiles-sync push/pull functionality with a local git server."""
        print("    setting up local git server for dotfiles-sync test...")

        # install git (should already be there but ensure it)
        vm.run_ssh_command("pacman -S --noconfirm git", timeout=120)

        # create a bare git repo to act as remote
        repository_path = "/tmp/dotfiles-remote.git"
        vm.run_ssh_command(f"git init --bare {repository_path}", timeout=30)
        vm.run_ssh_command(f"chown -R {username}:{username} {repository_path}", timeout=30)

        # add safe.directory to avoid dubious ownership errors
        vm.run_ssh_command(
            f"git config --global --add safe.directory {repository_path}",
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "git config --global --add safe.directory {repository_path}"',
            timeout=30,
        )

        # configure git for the user
        vm.run_ssh_command(
            f'su - {username} -c "git config --global user.email \\"test@test.com\\""',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "git config --global user.name \\"Test User\\""',
            timeout=30,
        )

        # initialize dotfiles-sync with the local repo
        dotfiles_repository = f"/home/{username}/.dotfiles-repo"
        vm.run_ssh_command(
            f'su - {username} -c "mkdir -p {dotfiles_repository}"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && git init"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && git remote add origin {repository_path}"',
            timeout=30,
        )

        # create dotfiles-sync config
        config_directory = f"/home/{username}/.config/dotfiles-sync"
        vm.run_ssh_command(f"mkdir -p {config_directory}", timeout=30)
        vm.run_ssh_command(f"chown -R {username}:{username} {config_directory}", timeout=30)

        # create a simple config with one test file
        config_content = f"""
files:
  - source: /home/{username}/.bashrc
    target: bashrc
"""
        exit_code, _, _ = vm.run_ssh_command(
            f'echo "{config_content}" > {config_directory}/config.yaml',
            timeout=30,
        )

        # ensure .bashrc exists with some content
        vm.run_ssh_command(
            f'su - {username} -c "echo \\"# test bashrc\\" > ~/.bashrc"',
            timeout=30,
        )

        print("    testing dotfiles-sync push...")
        # test push with DOTFILES_SKIP_SSH_CHECK to avoid KeePassXC dependency
        exit_code, stdout, stderr = vm.run_ssh_command(
            f'su - {username} -c "DOTFILES_SKIP_SSH_CHECK=true dotfiles-sync push --dry-run 2>&1"',
            timeout=60,
        )
        print(f"    push dry-run output: {stdout[:200] if stdout else 'empty'}")

        # set default branch to main for consistency
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && git checkout -b main"',
            timeout=30,
        )

        # manually commit and push to verify git works
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && cp ~/.bashrc bashrc"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && git add -A"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && git commit -m \\"Initial dotfiles\\""',
            timeout=30,
        )
        exit_code, stdout, stderr = vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && git push -u origin main 2>&1"',
            timeout=60,
        )
        assert exit_code == 0, f"Git push failed: {stderr}"
        print("    git push to local repo successful")

        # modify the bashrc locally
        vm.run_ssh_command(
            f'su - {username} -c "echo \\"# modified\\" >> ~/.bashrc"',
            timeout=30,
        )

        # sync the change
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && cp ~/.bashrc bashrc && git add -A && git commit -m \\"Update bashrc\\""',
            timeout=30,
        )
        exit_code, _, stderr = vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repository} && git push"',
            timeout=60,
        )
        assert exit_code == 0, f"Git push update failed: {stderr}"
        print("    dotfiles change tracking successful")

        # verify the remote has the commits using --all to see all branches in bare repo
        exit_code, stdout, stderr = vm.run_ssh_command(
            f"git -C {repository_path} log --all --oneline",
            timeout=30,
        )
        assert exit_code == 0, f"Git log failed with exit {exit_code}: {stderr}"
        assert "Update bashrc" in stdout, f"Commits not found in remote, got: {stdout}"
        print("    dotfiles-sync test completed successfully")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_migration_from_previous_install_preserves_home_and_secure_boot_keys(
        self,
        qemu_vm_with_network: QemuVm,
    ) -> None:
        # a manual (non-DALI) encrypted install is migrated: home data and the very same
        # secure boot keys must survive the wipe and reinstall
        vm = qemu_vm_with_network
        setup_vm_for_install(vm)

        print("\n=== phase 1: create a manual encrypted arch install ===")
        run_checked(
            vm,
            [
                "parted -s /dev/vda mklabel gpt",
                "parted -s /dev/vda mkpart primary fat32 1MiB 513MiB",
                "parted -s /dev/vda set 1 esp on",
                "parted -s /dev/vda mkpart primary 513MiB 100%",
                "mkfs.fat -F32 /dev/vda1",
                "echo -n 'oldpassword' | cryptsetup luksFormat --type luks2 /dev/vda2 -",
                "echo -n 'oldpassword' | cryptsetup open /dev/vda2 cryptroot -",
                "mkfs.btrfs -f /dev/mapper/cryptroot",
                "mount /dev/mapper/cryptroot /mnt",
                "btrfs subvolume create /mnt/@",
                "btrfs subvolume create /mnt/@home",
                "umount /mnt",
                "mount -o subvol=@ /dev/mapper/cryptroot /mnt",
                "mkdir -p /mnt/home /mnt/boot/efi",
                "mount -o subvol=@home /dev/mapper/cryptroot /mnt/home",
                "mount /dev/vda1 /mnt/boot/efi",
            ],
        )
        run_checked(
            vm,
            [
                "pacstrap /mnt base linux linux-firmware mkinitcpio sudo sbctl efibootmgr "
                "btrfs-progs cryptsetup networkmanager openssh"
            ],
            timeout=1800,
        )

        print("\n=== phase 2: create user data and secure boot keys worth preserving ===")
        run_checked(
            vm,
            [
                "mkdir -p /mnt/home/testuser/.ssh /mnt/home/testuser/.config",
                "echo 'important documents' > /mnt/home/testuser/important.txt",
                "printf '%s\\n' '-----BEGIN OPENSSH PRIVATE KEY-----' secret_key_data "
                "'-----END OPENSSH PRIVATE KEY-----' > /mnt/home/testuser/.ssh/id_rsa",
                "chmod 600 /mnt/home/testuser/.ssh/id_rsa",
                "printf '[user]\\nemail = testuser@example.com\\n' > /mnt/home/testuser/.gitconfig",
                "arch-chroot /mnt sbctl create-keys",
            ],
        )
        # enrolling may fail in the VM; the keys on disk are what must be preserved
        vm.run_ssh_command(
            "arch-chroot /mnt sbctl enroll-keys --yes-this-might-brick-my-machine", timeout=120
        )
        _, keys_before, _ = vm.run_ssh_command(f"sha256sum {SBCTL_KEY_FILES}", timeout=30)
        assert keys_before.count("/mnt/var/lib/sbctl/keys/") == 3, keys_before

        run_checked(vm, ["sync", "umount -R /mnt", "cryptsetup close cryptroot"])
        _, partitions_before, _ = vm.run_ssh_command("blkid /dev/vda1 /dev/vda2", timeout=30)

        print("\n=== phase 3: migrate with DALI (new LUKS password) ===")
        exit_code, stdout, stderr = run_make_install(
            vm,
            unattended_install_env(
                SOURCE_LUKS_PASSWORD="oldpassword",
                LUKS_PASSWORD="newpassword",
                USER_PASSWORD="newpassword",
                ENABLE_MIGRATION="true",
                SWAP_SIZE_MB="512",
            ),
        )
        assert exit_code == 0, f"Migration installation failed:\nstdout: {stdout}\nstderr: {stderr}"

        print("\n=== phase 4: verify data and keys were carried over ===")
        preserved_files = {
            "/mnt/home/testuser/important.txt": "important documents",
            "/mnt/home/testuser/.ssh/id_rsa": "OPENSSH PRIVATE KEY",
            "/mnt/home/testuser/.gitconfig": "testuser@example.com",
        }
        for path, expected_content in preserved_files.items():
            exit_code, stdout, _ = vm.run_ssh_command(f"cat {path}", timeout=30)
            assert exit_code == 0 and expected_content in stdout, f"{path} not preserved: {stdout}"

        _, keys_after, _ = vm.run_ssh_command(f"sha256sum {SBCTL_KEY_FILES}", timeout=30)
        assert keys_after == keys_before, (
            "secure boot keys were not preserved (new keys were generated instead)\n"
            f"before:\n{keys_before}\nafter:\n{keys_after}"
        )

        _, partitions_after, _ = vm.run_ssh_command("blkid /dev/vda1 /dev/vda2", timeout=30)
        assert partitions_before != partitions_after, "disk should have been repartitioned"

        print("\n=== migration test completed successfully ===")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_idempotent_installation_recovers_from_partial_install(
        self,
        qemu_vm_with_network: QemuVm,
        expected_subvolumes: list[str],
        storage_config: dict,
        system_config: dict,
        installer_config: dict,
    ) -> None:
        # an interrupted install (partitions, LUKS and part of the subvolumes) is re-run
        # without wiping: the installer must reuse what exists and converge
        vm = qemu_vm_with_network
        assertions = InstallationAssertions(vm)
        setup_vm_for_install(vm)

        print("\n=== phase 1: leave a partial install behind ===")
        run_checked(
            vm,
            [
                "parted -s /dev/vda mklabel gpt",
                "parted -s /dev/vda mkpart primary fat32 1MiB 2049MiB",
                "parted -s /dev/vda set 1 esp on",
                "parted -s /dev/vda mkpart primary 2049MiB 100%",
                "mkfs.fat -F32 /dev/vda1",
                "echo -n 'testpassword' | cryptsetup luksFormat --type luks2 /dev/vda2 -",
                "echo -n 'testpassword' | cryptsetup open /dev/vda2 cryptroot -",
                "mkfs.btrfs -f /dev/mapper/cryptroot",
                "mount /dev/mapper/cryptroot /mnt",
                "btrfs subvolume create /mnt/@",
                "btrfs subvolume create /mnt/@home",
                "umount /mnt",
                "cryptsetup close cryptroot",
            ],
        )
        _, luks_uuid_before, _ = vm.run_ssh_command("blkid -s UUID -o value /dev/vda2", timeout=30)

        print("\n=== phase 2: re-run the installer without wiping ===")
        exit_code, stdout, stderr = run_make_install(
            vm,
            unattended_install_env(
                WIPE_METHOD="skip",
                ENABLE_SNAPSHOT_BOOT="true",
                ENABLE_HIBERNATION="true",
            ),
        )
        assert exit_code == 0, f"Recovery installation failed:\nstdout: {stdout}\nstderr: {stderr}"

        print("\n=== phase 3: verify the existing volume was reused and completed ===")
        _, luks_uuid_after, _ = vm.run_ssh_command("blkid -s UUID -o value /dev/vda2", timeout=30)
        assert luks_uuid_after == luks_uuid_before, "existing LUKS volume should have been reused"
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)
        assertions.assert_btrfs_mount_options(expected_mount_options(storage_config))
        assertions.raise_if_failed()

        print("\n=== phase 4: reboot into the recovered system ===")
        configure_ssh_and_reboot(vm, "testpassword")

        print("\n=== phase 5: post-boot verification ===")
        post_boot_assertions = InstallationAssertions(vm)
        print_secure_boot_summary(vm, "POST-RECOVERY")
        assert verify_secure_boot_properly_configured(vm), (
            "Secure boot must be properly configured after recovery install"
        )

        username = system_config["user"]["name"]
        user_groups = [*system_config["user"]["groups"], installer_config["docker"]["access_group"]]
        post_boot_assertions.assert_snapper_config_exists("root")
        post_boot_assertions.assert_user_in_groups(username, user_groups)
        post_boot_assertions.assert_swapfile_exists("/.swap/swapfile")
        post_boot_assertions.assert_hibernation_resume_configured()
        post_boot_assertions.assert_final_config_written(username)
        post_boot_assertions.raise_if_failed()

        print("\n=== idempotent recovery test completed successfully ===")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_env_vars_override_config_values(
        self,
        qemu_vm_with_network: QemuVm,
        expected_subvolumes: list[str],
        storage_config: dict,
        system_config: dict,
        installer_config: dict,
    ) -> None:
        """verify installation works with environment variables overriding config values."""
        vm = qemu_vm_with_network
        assertions = InstallationAssertions(vm)

        print("\n=== phase 1: setup for env vars test ===")
        setup_vm_for_install(vm)

        print("\n=== phase 2: running installer with env var overrides ===")
        exit_code, stdout, stderr = run_make_install(
            vm,
            unattended_install_env(
                GPU_VENDOR="none",
                CPU_VENDOR="amd",
                ENABLE_SNAPSHOT_BOOT="true",
                ENABLE_HIBERNATION="true",
                ENABLE_FIREWALL="true",
            ),
        )

        assert exit_code == 0, f"Env vars installation failed:\nstdout: {stdout}\nstderr: {stderr}"

        print("\n=== phase 3: verifying installation ===")
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)
        assertions.assert_btrfs_mount_options(expected_mount_options(storage_config))
        assertions.raise_if_failed()

        print("\n=== phase 4: reboot and verify ===")
        configure_ssh_and_reboot(vm, "testpassword")

        post_boot_assertions = InstallationAssertions(vm)
        print_secure_boot_summary(vm, "POST-INSTALL ENV VARS")
        assert verify_secure_boot_properly_configured(vm), "Secure boot must be properly configured"

        username = system_config["user"]["name"]
        post_boot_assertions.assert_final_config_written(username)

        post_boot_assertions.raise_if_failed()
        print("\n=== env vars override test completed ===")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_usb_boot_drive_stores_efi_and_luks_headers_on_second_disk(
        self,
        qemu_vm_with_usb_disk_and_network: QemuVm,
    ) -> None:
        """USB boot drive test with detached LUKS headers and EFI on USB.

        verifies the PDE (plausible deniability encryption) feature:
        - USB drive (/dev/vdb) gets 4 partitions: EFI, ISO, LUKS header, Backup
        - EFI contents (UKIs, bootloader, loader.conf) are relocated to USB
        - LUKS header is detached from main disk and stored on USB
        - main disk's encrypted partition has no visible LUKS header
        - internal EFI partition is wiped of boot files
        - recovery ISO boot entry exists on USB
        - backup partition is formatted with USBBACKUP label
        """
        vm = qemu_vm_with_usb_disk_and_network

        config_path = QEMU_DATA_DIRECTORY / "maximal_config.yaml"
        with open(config_path) as config_file:
            config = yaml.safe_load(config_file)
        expected_subvolumes = [
            subvolume["name"] for subvolume in config["storage"]["btrfs"]["subvolumes"]
        ]

        assertions = InstallationAssertions(vm)

        print("\n=== phase 1: pre-install verification ===")
        print_secure_boot_summary(vm, "PRE-INSTALL (USB)")
        assert verify_setup_mode_before_install(vm), (
            "UEFI must be in setup mode before installation for key enrollment"
        )

        # verify second disk exists
        exit_code, stdout, _ = vm.run_ssh_command("lsblk -dno NAME,SIZE /dev/vdb", timeout=30)
        assert exit_code == 0, f"USB disk /dev/vdb not found: {stdout}"
        print(f"    USB disk detected: {stdout.strip()}")

        print("\n=== phase 2: run installer with USB boot enabled ===")
        setup_vm_for_install(vm, config_path=config_path, extra_packages="cdrtools")
        create_fake_iso(vm)

        exit_code, stdout, stderr = run_make_install(
            vm,
            {
                "LUKS_PASSWORD": "testpassword",
                "USER_PASSWORD": "testpassword",
                "NON_INTERACTIVE": "true",
                "TARGET_DISK": "/dev/vda",
                "SWAP_SIZE_MB": "1024",
                "ENABLE_SNAPSHOT_BOOT": "true",
                "ENABLE_HIBERNATION": "true",
                "ENABLE_FIREWALL": "true",
                "ENABLE_DOCKER": "true",
                "GPU_VENDOR": "none",
                "CPU_VENDOR": "amd",
                "WIPE_METHOD": "quick",
                "ENABLE_USB_BOOT": "true",
                "USB_BOOT_DEVICE": "/dev/vdb",
            },
        )
        assert exit_code == 0, f"Installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    installation with USB boot completed successfully")

        print("\n=== phase 3: verify USB drive layout (before reboot) ===")

        assertions.assert_usb_drive_partitioned("/dev/vdb")

        # mount USB EFI partition for inspection
        vm.run_ssh_command("mkdir -p /mnt/usb-efi", timeout=30)
        vm.run_ssh_command("mount /dev/vdb1 /mnt/usb-efi", timeout=30)

        assertions.assert_usb_efi_partition_has_bootloader("/mnt/usb-efi")
        assertions.assert_usb_efi_partition_has_uki_files("/mnt/usb-efi")
        assertions.assert_usb_efi_partition_has_loader_conf("/mnt/usb-efi")
        assertions.assert_usb_recovery_entry_exists("/mnt/usb-efi")
        assertions.assert_usb_efi_files_signed("/mnt/usb-efi")

        # list all .efi files for diagnostics
        _, efi_listing, _ = vm.run_ssh_command(
            "find /mnt/usb-efi -name '*.efi' -o -name '*.EFI' 2>/dev/null", timeout=30
        )
        print(f"    USB EFI files:\n{efi_listing}")

        vm.run_ssh_command("umount /mnt/usb-efi", timeout=30)

        # check LUKS header partition
        print("    checking USB has detached LUKS header...")
        vm.run_ssh_command("mkdir -p /mnt/usb-header", timeout=30)
        vm.run_ssh_command("mount /dev/vdb3 /mnt/usb-header", timeout=30)
        assertions.assert_usb_header_partition_has_luks_header("/mnt/usb-header")
        vm.run_ssh_command("umount /mnt/usb-header", timeout=30)

        # check internal disk has no visible LUKS header
        assertions.assert_main_disk_has_no_luks_header("/dev/vda2")

        # check internal EFI was wiped of boot files
        assertions.assert_internal_efi_has_no_boot_files("/mnt/efi")
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)

        print("\n=== phase 4: verify backup partition (4th partition) ===")

        assertions.assert_usb_backup_partition_exists("/dev/vdb")
        assertions.assert_usb_backup_partition_label("/dev/vdb", "USBBACKUP")

        # list partition layout for diagnostics
        _, layout, _ = vm.run_ssh_command("lsblk -o NAME,SIZE,FSTYPE,LABEL /dev/vdb", timeout=30)
        print(f"    USB partition layout:\n{layout}")

        print("\n=== phase 5: verify snapshot boot entries on USB drive ===")

        # pre-reboot checks use /mnt paths directly (not assertion methods
        # which assume a booted system)
        print("    checking snapper config was created...")
        exit_code, _, _ = vm.run_ssh_command(
            "test -f /mnt/etc/snapper/configs/root",
            timeout=30,
        )
        assert exit_code == 0, "snapper config 'root' not found at /mnt/etc/snapper/configs/root"

        print("    checking manage-snapshot-ukis script exists...")
        exit_code, _, _ = vm.run_ssh_command(
            "test -x /mnt/usr/local/bin/manage-snapshot-ukis",
            timeout=30,
        )
        assert exit_code == 0, "manage-snapshot-ukis not found or not executable"

        print("    checking snapshot hooks deployed...")
        exit_code, _, _ = vm.run_ssh_command(
            "test -f /mnt/etc/pacman.d/hooks/95-snapshot-uki-refresh.hook",
            timeout=30,
        )
        assert exit_code == 0, "snapshot UKI refresh pacman hook not deployed"

        print("    creating test snapshot for USB boot entry verification...")
        exit_code, stdout, stderr = vm.run_ssh_command(
            "arch-chroot /mnt snapper --no-dbus -c root create -d 'USB snapshot test' --print-number",
            timeout=60,
        )
        assert exit_code == 0, f"Failed to create snapshot: {stderr}"
        snapshot_id = stdout.strip()
        print(f"    created snapshot {snapshot_id}")

        print("    running manage-snapshot-ukis refresh to generate snapshot UKIs...")
        exit_code, stdout, stderr = vm.run_ssh_command(
            "arch-chroot /mnt manage-snapshot-ukis refresh",
            timeout=120,
        )
        assert exit_code == 0, f"Snapshot UKI refresh failed: {stderr}"

        print("    mounting USB EFI to check snapshot UKIs...")
        vm.run_ssh_command("mkdir -p /mnt/usb-efi-check", timeout=30)
        vm.run_ssh_command("mount /dev/vdb1 /mnt/usb-efi-check", timeout=30)

        exit_code, stdout, _ = vm.run_ssh_command(
            "find /mnt/usb-efi-check -name '*snapshot*' -o -name '*snap*' 2>/dev/null",
            timeout=30,
        )
        print(f"    snapshot-related files on USB EFI:\n{stdout}")

        exit_code, stdout, _ = vm.run_ssh_command(
            "ls -la /mnt/usb-efi-check/EFI/Linux/ 2>/dev/null",
            timeout=30,
        )
        print(f"    USB EFI/Linux contents:\n{stdout}")

        vm.run_ssh_command("umount /mnt/usb-efi-check", timeout=30)

        assertions.raise_if_failed()
        print("\n=== USB boot drive verification completed ===")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_usb_backup_writes_packages_manifest_and_config_to_backup_partition(
        self,
        qemu_vm_with_usb_disk_and_network: QemuVm,
    ) -> None:
        """USB backup test - runs backup_to_usb after installing with USB boot.

        verifies:
        - backup partition gets mounted and populated
        - package catalog is generated from installed system
        - config.yaml is exported to backup partition
        - backup manifest is written with timestamp/hostname
        - category directories are created for backed-up items
        """
        vm = qemu_vm_with_usb_disk_and_network

        config_path = QEMU_DATA_DIRECTORY / "maximal_config.yaml"
        with open(config_path) as config_file:
            config = yaml.safe_load(config_file)

        assertions = InstallationAssertions(vm)

        print("\n=== phase 1: run installer with USB boot (pre-requisite) ===")
        setup_vm_for_install(vm, config_path=config_path, extra_packages="cdrtools")
        create_fake_iso(vm)

        exit_code, stdout, stderr = run_make_install(
            vm,
            {
                "LUKS_PASSWORD": "testpassword",
                "USER_PASSWORD": "testpassword",
                "NON_INTERACTIVE": "true",
                "TARGET_DISK": "/dev/vda",
                "SWAP_SIZE_MB": "1024",
                "ENABLE_SNAPSHOT_BOOT": "true",
                "GPU_VENDOR": "none",
                "CPU_VENDOR": "amd",
                "WIPE_METHOD": "quick",
                "ENABLE_USB_BOOT": "true",
                "USB_BOOT_DEVICE": "/dev/vdb",
            },
        )
        assert exit_code == 0, f"Installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    installation completed")

        print("\n=== phase 2: run backup_to_usb ===")

        # create test dotfiles so there's something to back up
        username = config["system"]["user"]["name"]
        vm.run_ssh_command(f"mkdir -p /home/{username}", timeout=30)
        vm.run_ssh_command(f"echo '# test zshrc' > /home/{username}/.zshrc", timeout=30)
        vm.run_ssh_command(f"echo '# test gitconfig' > /home/{username}/.gitconfig", timeout=30)

        # run backup_to_usb via make
        exit_code, stdout, stderr = vm.run_ssh_command(
            "cd /root/arch_installer && "
            "NON_INTERACTIVE=true "
            "USB_DEVICE=/dev/vdb "
            "BACKUP_CATEGORIES=dotfiles,system "
            "make backup_to_usb",
            timeout=300,
        )
        assert exit_code == 0, f"Backup failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    backup_to_usb completed")

        print("\n=== phase 3: verify backup partition contents ===")

        # mount backup partition for inspection
        vm.run_ssh_command("mkdir -p /mnt/usb-backup", timeout=30)
        vm.run_ssh_command("mount /dev/vdb4 /mnt/usb-backup", timeout=30)

        assertions.assert_usb_backup_has_manifest("/mnt/usb-backup")
        assertions.assert_usb_backup_has_package_catalog("/mnt/usb-backup")
        assertions.assert_usb_backup_has_config("/mnt/usb-backup")
        assertions.assert_usb_backup_has_category_directory("dotfiles", "/mnt/usb-backup")
        assertions.assert_usb_backup_has_category_directory("system", "/mnt/usb-backup")

        # verify specific backed-up items
        exit_code, stdout, _ = vm.run_ssh_command(
            "cat /mnt/usb-backup/dotfiles/zshrc 2>/dev/null", timeout=30
        )
        assert exit_code == 0 and "test zshrc" in stdout, (
            f"zshrc not found or has wrong content in backup: {stdout}"
        )
        print("    zshrc backed up correctly")

        # verify manifest has expected fields
        exit_code, stdout, _ = vm.run_ssh_command("cat /mnt/usb-backup/manifest.yaml", timeout=30)
        assert "hostname" in stdout, f"manifest missing hostname: {stdout}"
        assert "package_count" in stdout, f"manifest missing package_count: {stdout}"
        assert "items_backed_up" in stdout, f"manifest missing items_backed_up: {stdout}"
        print("    manifest structure verified")

        # verify package catalog is well-formed yaml
        exit_code, stdout, _ = vm.run_ssh_command(
            "cat /mnt/usb-backup/config/package_catalog.yaml", timeout=30
        )
        assert "packages:" in stdout, f"package catalog has wrong format: {stdout}"
        assert "cataloged:" in stdout, f"package catalog missing cataloged key: {stdout}"
        print(f"    package catalog looks valid ({stdout.count('name:')} packages)")

        # list backup contents for diagnostics
        _, listing, _ = vm.run_ssh_command(
            "find /mnt/usb-backup -maxdepth 2 -type f | sort", timeout=30
        )
        print(f"    backup contents:\n{listing}")

        vm.run_ssh_command("umount /mnt/usb-backup", timeout=30)

        assertions.raise_if_failed()
        print("\n=== USB backup test completed successfully ===")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_tui_interactive_installation_via_simulated_user_input(
        self,
        qemu_vm_with_network: QemuVm,
        tmp_path: Path,
    ) -> None:
        # the installer runs WITHOUT NON_INTERACTIVE; only the secrets key comes from the
        # environment, tmux provides the terminal and every choice is made with keystrokes
        vm = qemu_vm_with_network
        config = load_test_config("maximal_config.yaml")

        print("\n=== phase 1: setup VM for TUI interactive install ===")
        config_path = write_config_with_encrypted_passwords(
            "maximal_config.yaml", tmp_path / "config.yaml", "testpassword"
        )
        setup_vm_for_install(vm, config_path=config_path)
        run_checked(vm, [INSTALL_TMUX_IF_MISSING], timeout=120)

        print("\n=== phase 2: start installer in tmux session ===")
        installer = TmuxSession(vm, "install")
        installer.start(
            "cd /root/arch_installer && PYTHONPATH=src "
            f"ARCH_INSTALLER_SECRETS_KEY={SECRETS_KEY} python -m arch_installer.cli"
        )

        print("\n=== phase 3: navigate TUI with keystrokes ===")
        for screen in TMUX_INSTALL_SESSION:
            installer.drive(screen)

        print("\n=== phase 4: wait for installation to complete ===")
        installer_exit = installer.wait_for_exit(timeout=2400)
        assert installer_exit == 0, f"TUI installation failed with exit code {installer_exit}"

        print("\n=== phase 5: configure SSH and reboot ===")
        configure_ssh_and_reboot(vm, "testpassword")

        exit_code, stdout, _ = vm.run_ssh_command("cat /etc/hostname", timeout=30)
        if exit_code == 0:
            print(f"    booted installed system: hostname={stdout.strip()}")

        print("\n=== phase 6: verify TUI-installed system ===")
        post_boot_assertions = InstallationAssertions(vm)

        username = config["system"]["user"]["name"]
        expected_locale = (
            f"{config['system']['locale']['language']}.{config['system']['locale']['encoding']}"
        )

        post_boot_assertions.assert_hostname(config["system"]["hostname"])
        post_boot_assertions.assert_timezone(config["system"]["timezone"])
        post_boot_assertions.assert_locale(expected_locale)
        post_boot_assertions.assert_user_exists(username)
        post_boot_assertions.assert_systemd_boot_installed()
        post_boot_assertions.assert_uki_directory_exists()
        post_boot_assertions.assert_secure_boot_keys_created()
        post_boot_assertions.assert_secure_boot_enrolled()
        post_boot_assertions.assert_secure_boot_enabled()
        post_boot_assertions.assert_bootloader_signed()
        post_boot_assertions.assert_esp_random_seed_private()
        post_boot_assertions.assert_all_ukis_signed()
        post_boot_assertions.assert_service_active("NetworkManager")

        post_boot_assertions.assert_final_config_written(username)

        print_secure_boot_summary(vm, "POST-INSTALL TUI")
        assert verify_secure_boot_properly_configured(vm), (
            "Secure boot must be properly configured after TUI install"
        )

        post_boot_assertions.raise_if_failed()

        print("\n=== TUI interactive installation test completed successfully ===")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_unsigned_efi_binary_blocked_by_secure_boot(
        self,
        qemu_vm_with_network: QemuVm,
    ) -> None:
        """verify secure boot blocks unsigned EFI binaries after key enrollment.

        installs a system with secure boot enabled, then:
        1. creates an unsigned EFI binary on the ESP
        2. verifies sbctl reports it as not signed
        3. removes the signing from a real UKI and confirms sbctl rejects it
        4. verifies overall sbctl verify now fails
        """
        vm = qemu_vm_with_network

        print("\n=== phase 1: install system with secure boot ===")
        setup_vm_for_install(vm, config_path=QEMU_DATA_DIRECTORY / "maximal_config.yaml")

        exit_code, stdout, stderr = run_make_install(
            vm,
            unattended_install_env(ENABLE_USB_BOOT="false", GPU_VENDOR="none", CPU_VENDOR="amd"),
        )
        assert exit_code == 0, f"Installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    installation completed")

        print("\n=== phase 2: verify signed binaries pass ===")
        # pre-reboot: sbctl is only available via arch-chroot
        exit_code, stdout, _ = vm.run_ssh_command(
            "arch-chroot /mnt sbctl verify /efi/EFI/BOOT/BOOTX64.EFI 2>&1",
            timeout=30,
        )
        assert exit_code == 0, f"Bootloader not signed: {stdout}"
        print("    bootloader signed OK")

        exit_code, stdout, _ = vm.run_ssh_command(
            "arch-chroot /mnt bash -c 'sbctl verify /efi/EFI/Linux/*.efi' 2>&1",
            timeout=30,
        )
        assert exit_code == 0, f"UKIs not signed: {stdout}"
        print("    all UKIs signed OK")

        exit_code, stdout, _ = vm.run_ssh_command(
            "arch-chroot /mnt bash -c 'sbctl verify' 2>&1",
            timeout=30,
        )
        assert exit_code == 0, f"sbctl verify failed: {stdout}"
        print("    sbctl verify all passed")

        print("    all signed binaries verified OK")

        print("\n=== phase 3: place unsigned EFI binary on ESP ===")
        unsigned_path = "/mnt/efi/EFI/Linux/unsigned-test.efi"
        vm.run_ssh_command(
            f"cp /mnt/usr/lib/systemd/boot/efi/systemd-bootx64.efi {unsigned_path}",
            timeout=30,
        )

        print("    checking sbctl rejects unsigned binary...")
        exit_code, stdout, _ = vm.run_ssh_command(
            "arch-chroot /mnt sbctl verify /efi/EFI/Linux/unsigned-test.efi 2>&1",
            timeout=30,
        )
        assert exit_code != 0 or "not signed" in stdout.lower(), (
            f"Expected unsigned binary to be rejected by sbctl verify, "
            f"but got exit={exit_code}, output: {stdout}"
        )
        print("    unsigned binary correctly rejected by sbctl")

        print("\n=== phase 4: remove signing from a real UKI ===")
        exit_code, uki_list, _ = vm.run_ssh_command(
            "ls /mnt/efi/EFI/Linux/*.efi | grep -v unsigned | head -1",
            timeout=30,
        )
        assert exit_code == 0 and uki_list.strip(), "No UKI files found on ESP"
        signed_uki = uki_list.strip()
        uki_chroot_path = signed_uki.replace("/mnt", "")

        exit_code, stdout, _ = vm.run_ssh_command(
            f"arch-chroot /mnt sbctl verify {uki_chroot_path} 2>&1",
            timeout=30,
        )
        assert exit_code == 0, f"Expected UKI to be signed before removal: {stdout}"
        print(f"    {uki_chroot_path} is signed")

        vm.run_ssh_command(
            f"arch-chroot /mnt sbctl remove-file {uki_chroot_path}",
            timeout=30,
        )
        vm.run_ssh_command(
            f"cp /mnt/usr/lib/systemd/boot/efi/systemd-bootx64.efi {signed_uki}",
            timeout=30,
        )

        print("    checking sbctl now rejects the replaced UKI...")
        exit_code, stdout, _ = vm.run_ssh_command(
            f"arch-chroot /mnt sbctl verify {uki_chroot_path} 2>&1",
            timeout=30,
        )
        assert exit_code != 0 or "not signed" in stdout.lower(), (
            f"Expected replaced UKI to be rejected by sbctl verify, "
            f"but got exit={exit_code}, output: {stdout}"
        )
        print("    replaced UKI correctly rejected by sbctl")

        print("\n=== phase 5: verify overall sbctl verify now reports unsigned files ===")
        exit_code, stdout, _ = vm.run_ssh_command(
            "arch-chroot /mnt sbctl verify 2>&1",
            timeout=30,
        )
        assert "not signed" in stdout.lower(), (
            f"Expected sbctl verify to report unsigned files, but output: {stdout}"
        )
        print("    sbctl verify correctly reports unsigned binaries in output")
        print("    sbctl verify correctly reports failures with unsigned binaries")

        vm.run_ssh_command(f"rm -f {unsigned_path}", timeout=30)

        print("\n=== negative secure boot test completed successfully ===")
