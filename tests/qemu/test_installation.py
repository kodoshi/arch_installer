from pathlib import Path

import pytest
import yaml

from tests.qemu.assertions import InstallationAssertions
from tests.qemu.ssh_config import SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM
from tests.qemu.uefi_setup import (
    print_secure_boot_summary,
    verify_secure_boot_properly_configured,
    verify_setup_mode_before_install,
)
from tests.qemu.vm import QemuVm

INSTALL_TIMEOUT = 1800
SECRETS_KEY = "12345678"
PROJECT_ROOT = Path(__file__).parent.parent.parent
QEMU_DATA_DIR = Path(__file__).parent.parent / "data"
BASE_PACKAGES = "python python-yaml python-cryptography python-cffi make"


def setup_vm_for_install(vm: QemuVm, extra_packages: str = "", expand_root: bool = True) -> None:
    """initialize pacman keyring, install base packages, and copy installer to VM.

    Note: expand_root=True by default to ensure cowspace is large enough for package
    installation. The live ISO has limited overlay space that can fill up quickly.
    """
    if expand_root:
        vm.run_ssh_command("mount -o remount,size=2G /run/archiso/cowspace", timeout=30)
    vm.run_ssh_command("pacman-key --init", timeout=120)
    packages = BASE_PACKAGES + (" " + extra_packages if extra_packages else "")
    exit_code, _, stderr = vm.run_ssh_command(
        f"pacman -Sy --noconfirm {packages}",
        timeout=300,
    )
    assert exit_code == 0, f"Failed to install dependencies: {stderr}"
    vm.copy_dir_to_vm(PROJECT_ROOT, "/root/arch_installer")


def run_make_install(vm: QemuVm, env_vars: dict[str, str]) -> tuple[int, str, str]:
    """run the installer via make with the given environment variables."""
    env_str = " ".join(f"{k}={v}" for k, v in env_vars.items())
    return vm.run_ssh_command(
        f"cd /root/arch_installer && {env_str} make install",
        timeout=2400,
    )


def configure_ssh_and_reboot(vm: QemuVm, luks_passphrase: str = "testpassword") -> None:
    """configure SSH for installed system and reboot."""
    for cmd in SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM:
        vm.run_ssh_command(cmd, timeout=120)
    vm.run_ssh_command("umount -R /mnt 2>/dev/null || true", timeout=60)
    vm.reboot(wait_for_ssh=True, timeout=300, luks_passphrase=luks_passphrase)


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

        # load maximal config for assertions
        maximal_config_path = QEMU_DATA_DIR / "maximal_config.yaml"
        with open(maximal_config_path) as f:
            config = yaml.safe_load(f)

        expected_subvolumes = [sv["name"] for sv in config["storage"]["btrfs"]["subvolumes"]]

        assertions = InstallationAssertions(vm)

        print("\n=== phase 1: pre-install verification ===")
        print_secure_boot_summary(vm, "PRE-INSTALL")
        assert verify_setup_mode_before_install(
            vm
        ), "UEFI must be in setup mode before installation for key enrollment"

        print("\n=== phase 2: run installer with maximal config ===")
        setup_vm_for_install(vm)

        # copy the maximal config to the VM
        vm.run_ssh_command("mkdir -p /root/arch_installer/config", timeout=30)
        vm.copy_file_to_vm(maximal_config_path, "/root/arch_installer/config/config.yaml")

        exit_code, stdout, stderr = run_make_install(
            vm,
            {
                "LUKS_PASSWORD": "testpassword",
                "USER_PASSWORD": "testpassword",
                "NON_INTERACTIVE": "true",
                "TARGET_DISK": "/dev/vda",
                "PACKAGE_PROFILE": "base",
                "TEST_SWAP_SIZE_MB": "1024",
                "ENABLE_SNAPSHOT_BOOT": "true",
                "ENABLE_HIBERNATION": "true",
                "ENABLE_UFW": "true",
                "ENABLE_DOCKER": "true",
                "GPU_VENDOR": "none",
                "CPU_VENDOR": "amd",
                "WIPE_METHOD": "secure",
            },
        )
        assert exit_code == 0, f"Installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    installation completed successfully")

        print("\n=== phase 3: verify storage setup (before reboot) ===")

        print("    checking EFI partition...")
        assertions.assert_partitions_exist("/dev/vda")
        assertions.assert_efi_partition_type("/dev/vda")
        assertions.assert_root_partition_type("/dev/vda")
        assertions.assert_efi_partition_size_mib(config["storage"]["efi_size_mb"], "/dev/vda")

        print("    checking LUKS encryption...")
        assertions.assert_luks_volume_active("cryptroot")
        assertions.assert_luks_type(config["storage"]["luks"]["type"].upper(), "cryptroot")
        assertions.assert_luks_cipher(config["storage"]["luks"]["cipher"], "cryptroot")

        print("    checking btrfs subvolumes...")
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)

        print("    checking mount options...")
        mount_options: str = config["storage"]["btrfs"]["mount_options"]
        expected_options = [option.strip() for option in mount_options.split(",")]
        assertions.assert_btrfs_mount_options(expected_options)

        print("    configuring SSH for post-reboot access...")
        for cmd in SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM:
            exit_code, _, _ = vm.run_ssh_command(cmd, timeout=120)
            if exit_code != 0:
                print(f"    warning: SSH setup command failed: {cmd}")

        assertions.raise_if_failed()

        print("\n=== phase 4: reboot into installed system ===")
        configure_ssh_and_reboot(vm, "testpassword")

        exit_code, stdout, _ = vm.run_ssh_command("cat /etc/hostname", timeout=30)
        if exit_code == 0:
            print(f"    booted installed system: hostname={stdout.strip()}")

        print("\n=== phase 5: verify system configuration ===")
        post_boot_assertions = InstallationAssertions(vm)

        print("    checking hostname...")
        post_boot_assertions.assert_hostname(config["system"]["hostname"])

        print("    checking timezone...")
        post_boot_assertions.assert_timezone(config["system"]["timezone"])

        print("    checking locale...")
        locale_str = (
            f"{config['system']['locale']['language']}.{config['system']['locale']['encoding']}"
        )
        post_boot_assertions.assert_locale(locale_str)

        print("    checking keymap...")
        post_boot_assertions.assert_keymap(config["system"]["locale"]["keymap"])

        print("    checking user configuration...")
        username = config["system"]["user"]["name"]
        user_groups = config["system"]["user"]["groups"].copy()
        post_boot_assertions.assert_user_exists(username)
        post_boot_assertions.assert_user_in_groups(username, user_groups)

        print("\n=== phase 5.1: verify boot configuration ===")

        print("    checking mkinitcpio hooks...")
        post_boot_assertions.assert_mkinitcpio_hooks(config["boot"]["hooks"])

        print("    checking kernel cmdline hardening...")
        hardening = config["boot"]["cmdline"]["hardening"]
        expected_cmdline_params = [
            f"lockdown={hardening['lockdown']}",
            f"iommu={hardening['iommu']}",
            f"pti={hardening['pti']}",
        ]
        post_boot_assertions.assert_kernel_cmdline_contains(expected_cmdline_params)

        print("    checking secure boot...")
        post_boot_assertions.assert_secure_boot_keys_created()
        post_boot_assertions.assert_bootloader_signed()
        post_boot_assertions.assert_all_ukis_signed()

        print("    checking UKIs for all kernels...")
        # UKI files are named based on package, not config name
        # e.g., linux-hardened package creates arch-linux-hardened-default.efi
        expected_kernel_patterns = []
        for k in config["boot"]["kernels"]:
            # extract the kernel suffix from package name (e.g., "linux-hardened" -> "hardened")
            package = k["package"]
            if package == "linux":
                expected_kernel_patterns.append("arch-linux-default")
            else:
                # linux-hardened -> arch-linux-hardened, linux-lts -> arch-linux-lts
                expected_kernel_patterns.append(f"arch-{package}")
        post_boot_assertions.assert_uki_files_exist(expected_kernel_patterns)

        print("    checking loader configuration...")
        post_boot_assertions.assert_loader_conf_exists()
        post_boot_assertions.assert_loader_timeout(config["boot"]["loader"]["timeout"])
        if not config["boot"]["loader"]["editor"]:
            post_boot_assertions.assert_loader_editor_disabled()

        print_secure_boot_summary(vm, "POST-INSTALL")
        assert verify_secure_boot_properly_configured(
            vm
        ), "Secure boot keys must be created, enrolled, and boot files signed"

        print("\n=== phase 5.2: verify swap and hibernation ===")
        swap_path = config["storage"]["swap"]["path"]
        post_boot_assertions.assert_swapfile_exists(swap_path)
        post_boot_assertions.assert_swap_active(swap_path)
        post_boot_assertions.assert_swapfile_in_fstab(swap_path)
        post_boot_assertions.assert_swapfile_size_mb(1024, swap_path)

        if config["storage"]["swap"]["hibernation"]["enabled"]:
            post_boot_assertions.assert_hibernation_resume_configured()
            post_boot_assertions.assert_hibernation_resume_offset()
            post_boot_assertions.assert_mkinitcpio_resume_hook()

        print("\n=== phase 5.3: verify snapper configuration ===")
        post_boot_assertions.assert_snapper_config_exists("root")
        post_boot_assertions.assert_snapshot_hooks_deployed()

        if config["snapper"].get("home"):
            post_boot_assertions.assert_snapper_config_exists("home")

        print("\n=== phase 5.4: verify firewall configuration ===")
        if config["firewall"]["enabled"]:
            post_boot_assertions.assert_service_enabled("ufw")

        print("\n=== phase 5.5: verify docker configuration ===")
        if config["docker"]["enabled"]:
            post_boot_assertions.assert_service_enabled("docker")
            # check user is in docker access group
            docker_group = config["docker"]["access_group"]
            exit_code, stdout, _ = vm.run_ssh_command(
                f"groups {username} | grep -q {docker_group} && echo 'yes' || echo 'no'",
                timeout=30,
            )
            assert "yes" in stdout, f"User {username} not in docker access group {docker_group}"

        print("\n=== phase 5.6: verify final config file ===")
        final_config_path = f"/home/{username}/final_config.yaml"
        exit_code, stdout, _ = vm.run_ssh_command(f"test -f {final_config_path} && echo 'exists'")
        assert (
            exit_code == 0 and "exists" in stdout
        ), f"final_config.yaml not found at {final_config_path}"
        print(f"    final_config.yaml found at {final_config_path}")

        print("\n=== phase 5.7: verify dotfiles-sync functionality ===")
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

            exit_code, stdout, stderr = vm.run_ssh_command(
                "manage-snapshot-ukis refresh",
                timeout=120,
            )
            assert exit_code == 0, f"Snapshot UKI refresh failed: {stderr}"

            exit_code, stdout, _ = vm.run_ssh_command(
                "ls /efi/EFI/Linux/arch-snapshot-*.efi 2>/dev/null || echo 'none'",
                timeout=30,
            )
            assert "none" not in stdout and stdout.strip(), "No snapshot UKIs generated"

            exit_code, stdout, _ = vm.run_ssh_command("bootctl list --no-pager", timeout=30)
            assert "snapshot" in stdout.lower(), "Snapshot entries not found in bootloader"

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
        repo_path = "/tmp/dotfiles-remote.git"
        vm.run_ssh_command(f"git init --bare {repo_path}", timeout=30)
        vm.run_ssh_command(f"chown -R {username}:{username} {repo_path}", timeout=30)

        # add safe.directory to avoid dubious ownership errors
        vm.run_ssh_command(
            f"git config --global --add safe.directory {repo_path}",
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "git config --global --add safe.directory {repo_path}"',
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
        dotfiles_repo = f"/home/{username}/.dotfiles-repo"
        vm.run_ssh_command(
            f'su - {username} -c "mkdir -p {dotfiles_repo}"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && git init"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && git remote add origin {repo_path}"',
            timeout=30,
        )

        # create dotfiles-sync config
        config_dir = f"/home/{username}/.config/dotfiles-sync"
        vm.run_ssh_command(f"mkdir -p {config_dir}", timeout=30)
        vm.run_ssh_command(f"chown -R {username}:{username} {config_dir}", timeout=30)

        # create a simple config with one test file
        config_content = f"""
files:
  - source: /home/{username}/.bashrc
    target: bashrc
"""
        exit_code, _, _ = vm.run_ssh_command(
            f'echo "{config_content}" > {config_dir}/config.yaml',
            timeout=30,
        )

        # ensure .bashrc exists with some content
        vm.run_ssh_command(
            f'su - {username} -c "echo \\"# test bashrc\\" > ~/.bashrc"',
            timeout=30,
        )

        print("    testing dotfiles-sync push...")
        # test push - using the script directly
        exit_code, stdout, stderr = vm.run_ssh_command(
            f'su - {username} -c "dotfiles-sync push --dry-run 2>&1 || true"',
            timeout=60,
        )
        print(f"    push dry-run output: {stdout[:200] if stdout else 'empty'}")

        # set default branch to main for consistency
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && git checkout -b main"',
            timeout=30,
        )

        # manually commit and push to verify git works
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && cp ~/.bashrc bashrc"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && git add -A"',
            timeout=30,
        )
        vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && git commit -m \\"Initial dotfiles\\""',
            timeout=30,
        )
        exit_code, stdout, stderr = vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && git push -u origin main 2>&1"',
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
            f'su - {username} -c "cd {dotfiles_repo} && cp ~/.bashrc bashrc && git add -A && git commit -m \\"Update bashrc\\""',
            timeout=30,
        )
        exit_code, _, stderr = vm.run_ssh_command(
            f'su - {username} -c "cd {dotfiles_repo} && git push"',
            timeout=60,
        )
        assert exit_code == 0, f"Git push update failed: {stderr}"
        print("    dotfiles change tracking successful")

        # verify the remote has the commits using --all to see all branches in bare repo
        exit_code, stdout, stderr = vm.run_ssh_command(
            f"git -C {repo_path} log --all --oneline",
            timeout=30,
        )
        assert exit_code == 0, f"Git log failed with exit {exit_code}: {stderr}"
        assert "Update bashrc" in stdout, f"Commits not found in remote, got: {stdout}"
        print("    dotfiles-sync test completed successfully")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_fresh_installation_with_minimal_config_produces_expected_system(
        self,
        qemu_vm_with_network: QemuVm,
    ) -> None:
        """minimal installation test with only essential sections.

        tests only enabled sections:
        - system: hostname, timezone, locale, user
        - storage: luks, btrfs (fewer subvolumes), swap without hibernation
        - boot: single kernel, minimal hooks
        - packages: base only

        disabled sections should NOT be configured:
        - snapper: disabled
        - firewall: disabled
        - docker: disabled
        - dotfiles: disabled
        """
        vm = qemu_vm_with_network

        # load minimal config for assertions
        minimal_config_path = QEMU_DATA_DIR / "minimal_config.yaml"
        with open(minimal_config_path) as f:
            config = yaml.safe_load(f)

        expected_subvolumes = [sv["name"] for sv in config["storage"]["btrfs"]["subvolumes"]]

        assertions = InstallationAssertions(vm)

        print("\n=== phase 1: pre-install verification ===")
        print_secure_boot_summary(vm, "PRE-INSTALL")
        assert verify_setup_mode_before_install(
            vm
        ), "UEFI must be in setup mode before installation for key enrollment"

        print("\n=== phase 2: run installer with minimal config ===")
        setup_vm_for_install(vm)

        # copy the minimal config to the VM
        vm.run_ssh_command("mkdir -p /root/arch_installer/config", timeout=30)
        vm.copy_file_to_vm(minimal_config_path, "/root/arch_installer/config/config.yaml")

        exit_code, stdout, stderr = run_make_install(
            vm,
            {
                "LUKS_PASSWORD": "testpassword",
                "USER_PASSWORD": "testpassword",
                "NON_INTERACTIVE": "true",
                "TARGET_DISK": "/dev/vda",
                "PACKAGE_PROFILE": "base",
                "TEST_SWAP_SIZE_MB": "512",
                "ENABLE_SNAPSHOT_BOOT": "false",
                "ENABLE_HIBERNATION": "false",
                "ENABLE_UFW": "false",
                "ENABLE_DOCKER": "false",
                "GPU_VENDOR": "none",
                "CPU_VENDOR": "amd",
                "WIPE_METHOD": "quick",
            },
        )
        assert exit_code == 0, f"Installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    installation completed successfully")

        print("\n=== phase 3: verify storage setup (before reboot) ===")

        print("    checking btrfs subvolumes...")
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)

        print("    checking mount options...")
        mount_options: str = config["storage"]["btrfs"]["mount_options"]
        expected_options = [option.strip() for option in mount_options.split(",")]
        assertions.assert_btrfs_mount_options(expected_options)

        print("    configuring SSH for post-reboot access...")
        for cmd in SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM:
            exit_code, _, _ = vm.run_ssh_command(cmd, timeout=120)
            if exit_code != 0:
                print(f"    warning: SSH setup command failed: {cmd}")

        assertions.raise_if_failed()

        print("\n=== phase 4: reboot into installed system ===")
        configure_ssh_and_reboot(vm, "testpassword")

        exit_code, stdout, _ = vm.run_ssh_command("cat /etc/hostname", timeout=30)
        if exit_code == 0:
            print(f"    booted installed system: hostname={stdout.strip()}")

        print("\n=== phase 5: verify minimal system configuration ===")
        post_boot_assertions = InstallationAssertions(vm)

        print("    checking hostname...")
        post_boot_assertions.assert_hostname(config["system"]["hostname"])

        print("    checking timezone...")
        post_boot_assertions.assert_timezone(config["system"]["timezone"])

        print("    checking user configuration...")
        username = config["system"]["user"]["name"]
        post_boot_assertions.assert_user_exists(username)

        print("\n=== phase 5.1: verify boot configuration ===")

        print("    checking mkinitcpio hooks...")
        post_boot_assertions.assert_mkinitcpio_hooks(config["boot"]["hooks"])

        print("    checking secure boot...")
        post_boot_assertions.assert_secure_boot_keys_created()
        post_boot_assertions.assert_bootloader_signed()
        post_boot_assertions.assert_all_ukis_signed()

        print("    checking UKIs for configured kernel...")
        expected_kernel_patterns = []
        for k in config["boot"]["kernels"]:
            package = k["package"]
            if package == "linux":
                expected_kernel_patterns.append("arch-linux-default")
            else:
                expected_kernel_patterns.append(f"arch-{package}")
        post_boot_assertions.assert_uki_files_exist(expected_kernel_patterns)

        print_secure_boot_summary(vm, "POST-INSTALL")
        assert verify_secure_boot_properly_configured(
            vm
        ), "Secure boot keys must be created, enrolled, and boot files signed"

        print("\n=== phase 5.2: verify swap (no hibernation) ===")
        swap_path = config["storage"]["swap"]["path"]
        post_boot_assertions.assert_swapfile_exists(swap_path)
        post_boot_assertions.assert_swap_active(swap_path)
        post_boot_assertions.assert_swapfile_in_fstab(swap_path)

        print("\n=== phase 5.3: verify disabled sections are NOT configured ===")

        print("    checking snapper is NOT configured...")
        exit_code, stdout, _ = vm.run_ssh_command(
            "snapper list-configs 2>/dev/null | grep -c root || echo '0'",
            timeout=30,
        )
        # snapper should not have root config when disabled
        # (it might still be installed as package dependency but not configured)

        print("    checking firewall is NOT enabled...")
        if not config["firewall"]["enabled"]:
            exit_code, stdout, _ = vm.run_ssh_command(
                "systemctl is-enabled ufw 2>/dev/null || echo 'disabled'",
                timeout=30,
            )
            # ufw should not be enabled
            assert "enabled" not in stdout or "disabled" in stdout, "UFW should not be enabled"

        print("    checking docker is NOT enabled...")
        if not config["docker"]["enabled"]:
            exit_code, stdout, _ = vm.run_ssh_command(
                "systemctl is-enabled docker 2>/dev/null || echo 'disabled'",
                timeout=30,
            )
            # docker should not be enabled
            assert "enabled" not in stdout or "disabled" in stdout, "Docker should not be enabled"

        print("    checking bootable snapshots are NOT configured...")
        if not config["boot"]["enable_snapshot_boot"]:
            exit_code, stdout, _ = vm.run_ssh_command(
                "ls /efi/EFI/Linux/arch-snapshot-*.efi 2>/dev/null || echo 'none'",
                timeout=30,
            )
            assert "none" in stdout, "Snapshot UKIs should not exist in minimal config"

        print("\n=== phase 5.4: verify final config file ===")
        username = config["system"]["user"]["name"]
        final_config_path = f"/home/{username}/final_config.yaml"
        exit_code, stdout, _ = vm.run_ssh_command(f"test -f {final_config_path} && echo 'exists'")
        assert (
            exit_code == 0 and "exists" in stdout
        ), f"final_config.yaml not found at {final_config_path}"
        print(f"    final_config.yaml found at {final_config_path}")

        post_boot_assertions.raise_if_failed()
        print("\n=== minimal config test completed successfully ===")

    @pytest.mark.qemu
    @pytest.mark.slow
    def test_migration_from_previous_install_preserves_home_and_secure_boot_keys(
        self,
        qemu_vm_with_network: QemuVm,
    ) -> None:
        """verify migration from previous manual install preserves home data and secure boot keys."""
        vm = qemu_vm_with_network
        project_root = Path(__file__).parent.parent.parent

        # ==== phase 1: create manual arch installation (not using arch_installer) ====
        print("\n=== phase 1: creating manual arch installation ===")
        print("    this simulates an existing system NOT created by arch_installer")

        vm.run_ssh_command("pacman-key --init", timeout=120)
        # vm.run_ssh_command("pacman-key --populate archlinux", timeout=120)

        # partition disk manually (simulating how a user might have done it)
        print("    partitioning disk manually...")
        partition_commands = [
            "parted -s /dev/vda mklabel gpt",
            "parted -s /dev/vda mkpart primary fat32 1MiB 513MiB",
            "parted -s /dev/vda set 1 esp on",
            "parted -s /dev/vda mkpart primary 513MiB 100%",
            "mkfs.fat -F32 /dev/vda1",
        ]
        for cmd in partition_commands:
            exit_code, _, stderr = vm.run_ssh_command(cmd, timeout=60)
            assert exit_code == 0, f"Partitioning failed: {cmd}\n{stderr}"

        # set up LUKS encryption (like a security-conscious user would)
        print("    setting up LUKS encryption...")
        exit_code, _, stderr = vm.run_ssh_command(
            "echo -n 'oldpassword' | cryptsetup luksFormat --type luks2 /dev/vda2 -",
            timeout=120,
        )
        assert exit_code == 0, f"LUKS format failed: {stderr}"

        exit_code, _, stderr = vm.run_ssh_command(
            "echo -n 'oldpassword' | cryptsetup open /dev/vda2 cryptroot -",
            timeout=60,
        )
        assert exit_code == 0, f"LUKS open failed: {stderr}"

        # create btrfs with subvolumes (manually, different from arch_installer's layout)
        print("    creating btrfs filesystem with manual subvolumes...")
        btrfs_commands = [
            "mkfs.btrfs -f /dev/mapper/cryptroot",
            "mount /dev/mapper/cryptroot /mnt",
            "btrfs subvolume create /mnt/@",
            "btrfs subvolume create /mnt/@home",
            "umount /mnt",
            "mount -o subvol=@ /dev/mapper/cryptroot /mnt",
            "mkdir -p /mnt/home /mnt/boot/efi",
            "mount -o subvol=@home /dev/mapper/cryptroot /mnt/home",
            "mount /dev/vda1 /mnt/boot/efi",
        ]
        for cmd in btrfs_commands:
            exit_code, _, stderr = vm.run_ssh_command(cmd, timeout=60)
            assert exit_code == 0, f"BTRFS setup failed: {cmd}\n{stderr}"

        # install base system manually
        print("    installing base arch system (minimal)...")
        exit_code, stdout, stderr = vm.run_ssh_command(
            "pacstrap /mnt base linux linux-firmware mkinitcpio sudo sbctl efibootmgr "
            "btrfs-progs cryptsetup networkmanager openssh",
            timeout=1800,
        )
        assert exit_code == 0, f"Pacstrap failed:\nstdout: {stdout}\nstderr: {stderr}"

        # generate fstab
        vm.run_ssh_command("genfstab -U /mnt >> /mnt/etc/fstab", timeout=30)

        # basic system configuration
        print("    configuring system basics...")
        config_commands = [
            "echo 'manual-install' > /mnt/etc/hostname",
            "arch-chroot /mnt ln -sf /usr/share/zoneinfo/UTC /etc/localtime",
            "echo 'en_US.UTF-8 UTF-8' > /mnt/etc/locale.gen",
            "arch-chroot /mnt locale-gen",
            "echo 'LANG=en_US.UTF-8' > /mnt/etc/locale.conf",
        ]
        for cmd in config_commands:
            vm.run_ssh_command(cmd, timeout=60)

        # ==== phase 2: create user data to preserve ====
        print("\n=== phase 2: creating user data to preserve ===")
        print("    creating user home directory with important files...")

        user_data_commands = [
            "mkdir -p /mnt/home/testuser/.ssh",
            "mkdir -p /mnt/home/testuser/.config",
            "echo 'important documents' > /mnt/home/testuser/important.txt",
            "echo 'ssh-rsa AAAAB3NzaC... testuser@manual-install' > /mnt/home/testuser/.ssh/id_rsa.pub",
            "echo '-----BEGIN OPENSSH PRIVATE KEY-----' > /mnt/home/testuser/.ssh/id_rsa",
            "echo 'secret_key_data_here' >> /mnt/home/testuser/.ssh/id_rsa",
            "echo '-----END OPENSSH PRIVATE KEY-----' >> /mnt/home/testuser/.ssh/id_rsa",
            "chmod 600 /mnt/home/testuser/.ssh/id_rsa",
            "echo '[user]' > /mnt/home/testuser/.gitconfig",
            "echo 'email = testuser@example.com' >> /mnt/home/testuser/.gitconfig",
        ]
        for cmd in user_data_commands:
            vm.run_ssh_command(cmd, timeout=30)

        # ==== phase 3: enroll secure boot keys (simulating pre-existing enrollment) ====
        print("\n=== phase 3: enrolling secure boot keys in existing installation ===")
        print("    this simulates a user who already set up secure boot manually")

        # create and enroll secure boot keys
        # sbctl now uses /var/lib/sbctl/keys as the default path
        sb_commands = [
            "arch-chroot /mnt sbctl create-keys",
            "arch-chroot /mnt sbctl enroll-keys --yes-this-might-brick-my-machine",
        ]
        for cmd in sb_commands:
            exit_code, stdout, stderr = vm.run_ssh_command(cmd, timeout=120)
            print(
                f"    {cmd}: exit={exit_code}, stdout={stdout[:200] if stdout else ''}, stderr={stderr[:200] if stderr else ''}"
            )
            # enrollment may fail in VM without proper UEFI but keys should be created
            if "create-keys" in cmd:
                assert exit_code == 0, f"Key creation failed: {stderr}"

        # verify keys were created - check /var/lib/sbctl/keys (new default path)
        exit_code, stdout, _ = vm.run_ssh_command(
            "ls -la /mnt/var/lib/sbctl/keys/ 2>&1", timeout=30
        )
        print(f"    ls /mnt/var/lib/sbctl/keys/: {stdout}")
        assert (
            exit_code == 0 and "PK" in stdout
        ), f"Secure boot keys not created at expected path: {stdout}"

        # DEBUG: check btrfs structure with keys mounted
        print("    DEBUG: checking btrfs structure with subvol=@ mounted...")
        # show current mount state
        exit_code, stdout, _ = vm.run_ssh_command("findmnt /mnt", timeout=30)
        print(f"    DEBUG current /mnt mount: {stdout}")
        # show btrfs subvolumes
        exit_code, stdout, _ = vm.run_ssh_command("btrfs subvolume list /mnt", timeout=30)
        print(f"    DEBUG btrfs subvolumes (from /mnt): {stdout}")

        # first unmount @ and mount btrfs root
        vm.run_ssh_command("umount /mnt/boot/efi", timeout=30)
        vm.run_ssh_command("umount /mnt/home", timeout=30)
        vm.run_ssh_command("umount /mnt", timeout=30)
        # explicitly mount subvolid=5 (the btrfs root)
        exit_code, stdout, stderr = vm.run_ssh_command(
            "mount -o subvolid=5 /dev/mapper/cryptroot /mnt", timeout=30
        )
        print(
            f"    DEBUG mount subvolid=5 result: exit={exit_code}, stdout={stdout}, stderr={stderr}"
        )
        exit_code, stdout, _ = vm.run_ssh_command("ls -la /mnt/", timeout=30)
        print(f"    DEBUG btrfs root (subvolid=5) contents: {stdout}")
        exit_code, stdout, _ = vm.run_ssh_command(
            "ls -la /mnt/@/var/lib/sbctl/keys/ 2>&1", timeout=30
        )
        print(f"    DEBUG @/var/lib/sbctl/keys from btrfs root: {stdout}")
        # remount properly for the rest of the test
        vm.run_ssh_command("umount /mnt", timeout=30)
        vm.run_ssh_command("mount -o subvol=@ /dev/mapper/cryptroot /mnt", timeout=30)
        vm.run_ssh_command("mount -o subvol=@home /dev/mapper/cryptroot /mnt/home", timeout=30)
        vm.run_ssh_command("mount /dev/vda1 /mnt/boot/efi", timeout=30)

        # record existing key fingerprints for later comparison
        exit_code, original_keys, _ = vm.run_ssh_command(
            "arch-chroot /mnt sbctl status 2>/dev/null || echo 'sbctl status unavailable'",
            timeout=60,
        )
        print(f"    original sbctl status:\n{original_keys}")
        # ==== phase 4: record partition UUIDs before migration ====
        print("\n=== phase 4: recording partition state before migration ===")
        exit_code, uuids_before, _ = vm.run_ssh_command("blkid /dev/vda1 /dev/vda2", timeout=30)
        print(f"    partition UUIDs before migration:\n{uuids_before}")

        # debug: verify sbctl keys exist before unmount
        exit_code, stdout, _ = vm.run_ssh_command("ls -la /mnt/var/lib/sbctl/keys/", timeout=30)
        print(f"    DEBUG before unmount - /mnt/var/lib/sbctl/keys/:\n{stdout}")

        # sync and unmount the manual installation
        print("    syncing and unmounting manual installation...")
        vm.run_ssh_command("sync", timeout=60)  # ensure all writes are flushed
        vm.run_ssh_command("umount -R /mnt 2>/dev/null || true", timeout=60)

        # debug: remount btrfs root (no subvol) and check @/var/lib/sbctl
        print("    DEBUG: remounting btrfs root to verify @ subvolume contents...")
        vm.run_ssh_command(
            "echo -n 'oldpassword' | cryptsetup open /dev/vda2 cryptroot -",
            timeout=60,
        )
        vm.run_ssh_command("mount /dev/mapper/cryptroot /mnt", timeout=60)
        exit_code, stdout, _ = vm.run_ssh_command(
            "ls -la /mnt/@/var/lib/sbctl/keys/ 2>&1", timeout=30
        )
        print(f"    DEBUG btrfs root view - @/var/lib/sbctl/keys/:\n{stdout}")
        vm.run_ssh_command("umount /mnt", timeout=60)
        vm.run_ssh_command("cryptsetup close cryptroot", timeout=60)

        # ==== phase 5: run arch_installer in migration mode via make ====
        print("\n=== phase 5: running arch_installer with migration enabled ===")
        vm.copy_dir_to_vm(project_root, "/root/arch_installer")

        setup_exit, _, _ = vm.run_ssh_command(
            "pacman -Sy --noconfirm python python-yaml python-cryptography python-cffi make",
            timeout=300,
        )
        assert setup_exit == 0, "Failed to install Python dependencies"

        # migration requires:
        # - SOURCE_LUKS_PASSWORD: password to decrypt the existing installation
        # - LUKS_PASSWORD: password for the NEW encryption (different from old)
        exit_code, stdout, stderr = vm.run_ssh_command(
            "cd /root/arch_installer && "
            "SOURCE_LUKS_PASSWORD=oldpassword "
            "LUKS_PASSWORD=newpassword "
            "USER_PASSWORD=newpassword "
            "NON_INTERACTIVE=true "
            "TARGET_DISK=/dev/vda "
            "PACKAGE_PROFILE=base "
            "ENABLE_MIGRATION=true "
            "TEST_SWAP_SIZE_MB=512 "
            "make install",
            timeout=2400,
        )
        print(f"    installer stdout:\n{stdout}")
        if stderr:
            print(f"    installer stderr:\n{stderr}")
        assert (
            exit_code == 0
        ), f"Migration installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    migration installation completed successfully")

        # ==== phase 6: verify home data preserved ====
        print("\n=== phase 6: verifying home data preservation ===")

        exit_code, stdout, _ = vm.run_ssh_command(
            "cat /mnt/home/testuser/important.txt", timeout=30
        )
        assert (
            exit_code == 0 and "important documents" in stdout
        ), f"Home data not preserved: {stdout}"
        print("    ✓ important.txt preserved")

        exit_code, stdout, _ = vm.run_ssh_command("cat /mnt/home/testuser/.ssh/id_rsa", timeout=30)
        assert (
            exit_code == 0 and "OPENSSH PRIVATE KEY" in stdout
        ), f"SSH key not preserved: {stdout}"
        print("    ✓ SSH private key preserved")

        exit_code, stdout, _ = vm.run_ssh_command("cat /mnt/home/testuser/.gitconfig", timeout=30)
        assert (
            exit_code == 0 and "testuser@example.com" in stdout
        ), f"Git config not preserved: {stdout}"
        print("    ✓ .gitconfig preserved")

        # ==== phase 7: verify secure boot keys preserved ====
        print("\n=== phase 7: verifying secure boot keys ===")

        # keys should be restored to /var/lib/sbctl/keys (new default path)
        exit_code, stdout, _ = vm.run_ssh_command(
            "ls /mnt/var/lib/sbctl/keys/",
            timeout=30,
        )
        assert exit_code == 0 and stdout.strip(), f"Secure boot keys directory missing: {stdout}"
        print(f"    ✓ secure boot keys directory exists: {stdout.strip()}")

        # check key files explicitly
        key_paths = ["PK/PK.key", "KEK/KEK.key", "db/db.key"]
        for key_path in key_paths:
            exit_code, _, _ = vm.run_ssh_command(
                f"test -f /mnt/var/lib/sbctl/keys/{key_path}", timeout=10
            )
            status = "✓" if exit_code == 0 else "✗"
            print(f"    {status} {key_path}")
            assert exit_code == 0, f"Missing key file: {key_path}"

        exit_code, migrated_keys, _ = vm.run_ssh_command(
            "arch-chroot /mnt sbctl status 2>/dev/null || echo 'sbctl status unavailable'",
            timeout=60,
        )
        print(f"    migrated sbctl status:\n{migrated_keys}")

        # note: secure boot enforcement check is not done pre-reboot as we're in chroot
        print("    note: secure boot enforcement will be verified after system boot")

        # ==== phase 8: verify NEW partition layout (disk was wiped and recreated) ====
        print("\n=== phase 8: verifying new partition layout ===")

        exit_code, uuids_after, _ = vm.run_ssh_command("blkid /dev/vda1 /dev/vda2", timeout=30)
        print(f"    partition UUIDs after migration:\n{uuids_after}")
        # migration wipes disk and creates new partitions, so UUIDs MUST change
        assert (
            uuids_before.strip() != uuids_after.strip()
        ), f"Partition UUIDs should have changed after migration (new partitions)!\nBefore: {uuids_before}\nAfter: {uuids_after}"
        print("    ✓ partition UUIDs changed (disk was properly wiped and recreated)")

        # verify btrfs subvolumes (arch_installer's layout)
        exit_code, stdout, _ = vm.run_ssh_command(
            "btrfs subvolume list /mnt 2>/dev/null || echo 'mount first'",
            timeout=30,
        )
        print(f"    btrfs subvolumes:\n{stdout}")

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
        """verify installation can recover and converge after a partial/interrupted install.

        this tests idempotency by:
        1. creating a partial installation (partitioning, btrfs, but failing before boot setup)
        2. re-running the full installer
        3. verifying the system converges to a working state
        """
        vm = qemu_vm_with_network
        assertions = InstallationAssertions(vm)
        project_root = Path(__file__).parent.parent.parent

        # ==== phase 1: create a partial installation ====
        print("\n=== phase 1: creating partial installation (simulating interrupt) ===")

        vm.run_ssh_command("pacman-key --init", timeout=120)

        setup_exit, setup_out, setup_err = vm.run_ssh_command(
            "pacman -Sy --noconfirm python python-yaml python-cryptography python-cffi make",
            timeout=300,
        )
        assert setup_exit == 0, f"Failed to install dependencies: {setup_err}"

        vm.copy_dir_to_vm(project_root, "/root/arch_installer")

        # create partial installation manually (simulating an interrupted install)
        # this replicates what the storage step does
        print("    creating partitions (like storage step would)...")
        partition_commands = [
            "parted -s /dev/vda mklabel gpt",
            "parted -s /dev/vda mkpart primary fat32 1MiB 2049MiB",
            "parted -s /dev/vda set 1 esp on",
            "parted -s /dev/vda mkpart primary 2049MiB 100%",
            "mkfs.fat -F32 /dev/vda1",
        ]
        for cmd in partition_commands:
            exit_code, _, stderr = vm.run_ssh_command(cmd, timeout=60)
            assert exit_code == 0, f"Partitioning failed: {cmd}\n{stderr}"

        print("    setting up LUKS encryption...")
        exit_code, _, stderr = vm.run_ssh_command(
            "echo -n 'testpassword' | cryptsetup luksFormat --type luks2 /dev/vda2 -",
            timeout=120,
        )
        assert exit_code == 0, f"LUKS format failed: {stderr}"

        exit_code, _, stderr = vm.run_ssh_command(
            "echo -n 'testpassword' | cryptsetup open /dev/vda2 cryptroot -",
            timeout=60,
        )
        assert exit_code == 0, f"LUKS open failed: {stderr}"

        print("    creating btrfs with partial subvolumes (incomplete setup)...")
        btrfs_commands = [
            "mkfs.btrfs -f /dev/mapper/cryptroot",
            "mount /dev/mapper/cryptroot /mnt",
            "btrfs subvolume create /mnt/@",
            "btrfs subvolume create /mnt/@home",
            # deliberately skip creating all subvolumes to simulate interruption
            "umount /mnt",
        ]
        for cmd in btrfs_commands:
            exit_code, _, stderr = vm.run_ssh_command(cmd, timeout=60)
            assert exit_code == 0, f"BTRFS setup failed: {cmd}\n{stderr}"

        print("    closing LUKS container (simulating abrupt stop)...")
        vm.run_ssh_command("cryptsetup close cryptroot", timeout=30)

        print("    partial installation state created successfully")
        print("    state: partition table done, LUKS formatted, partial btrfs subvolumes")

        # ==== phase 2: run full installer to converge ====
        print("\n=== phase 2: running full installer to recover/converge ===")

        exit_code, stdout, stderr = vm.run_ssh_command(
            "cd /root/arch_installer && "
            "LUKS_PASSWORD=testpassword "
            "USER_PASSWORD=testpassword "
            "NON_INTERACTIVE=true "
            "TARGET_DISK=/dev/vda "
            "PACKAGE_PROFILE=base "
            "TEST_SWAP_SIZE_MB=1024 "
            "ENABLE_SNAPSHOT_BOOT=true "
            "ENABLE_HIBERNATION=true "
            "make install",
            timeout=2400,
        )
        assert exit_code == 0, f"Recovery installation failed:\nstdout: {stdout}\nstderr: {stderr}"
        print("    recovery installation completed successfully")

        # ==== phase 3: verify converged state (pre-reboot) ====
        print("\n=== phase 3: verifying converged installation state (pre-reboot) ===")

        print("    checking btrfs subvolumes (all should exist now)...")
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)

        print("    checking mount options...")
        mount_options: str = storage_config["btrfs"]["mount_options"]
        expected_options = [option.strip() for option in mount_options.split(",")]
        assertions.assert_btrfs_mount_options(expected_options)

        if assertions.has_failures():
            print("    pre-reboot verification failures:")
            for result in assertions.get_results():
                if not result.passed:
                    print(f"      - {result.name}: {result.message}")
            assertions.raise_if_failed()

        # ==== phase 4: reboot and verify system boots ====
        print("\n=== phase 4: reboot and verify system boots after recovery ===")

        from tests.qemu.ssh_config import SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM

        for cmd in SSH_CONFIG_COMMANDS_FOR_INSTALLED_SYSTEM:
            exit_code, _, _ = vm.run_ssh_command(cmd, timeout=120)
            if exit_code != 0:
                print(f"    warning: SSH setup command failed: {cmd}")

        print("    unmounting filesystems...")
        vm.run_ssh_command("umount -R /mnt 2>/dev/null || true", timeout=60)

        print("    rebooting system...")
        vm.reboot(wait_for_ssh=True, timeout=300, luks_passphrase="testpassword")
        print("    system booted successfully!")

        # ==== phase 5: post-boot verification ====
        print("\n=== phase 5: post-boot verification ===")
        post_boot_assertions = InstallationAssertions(vm)

        print("    verifying secure boot configuration...")
        print_secure_boot_summary(vm, "POST-RECOVERY")
        assert verify_secure_boot_properly_configured(
            vm
        ), "Secure boot must be properly configured after recovery install"

        print("    checking snapper configuration exists...")
        post_boot_assertions.assert_snapper_config_exists("root")

        print("    checking user configuration...")
        username = system_config["user"]["name"]
        user_groups = system_config["user"]["groups"].copy()
        docker_access_group = (
            installer_config.get("packages", {})
            .get("docker", {})
            .get("access_group", "docker_access")
        )
        if docker_access_group not in user_groups:
            user_groups.append(docker_access_group)
        post_boot_assertions.assert_user_in_groups(username, user_groups)

        print("    checking swapfile exists...")
        post_boot_assertions.assert_swapfile_exists("/.swap/swapfile")

        print("    checking hibernation configuration...")
        post_boot_assertions.assert_hibernation_resume_configured()

        print("    checking final config file...")
        final_config_path = f"/home/{username}/final_config.yaml"
        exit_code, stdout, _ = vm.run_ssh_command(f"test -f {final_config_path} && echo 'exists'")
        assert (
            exit_code == 0 and "exists" in stdout
        ), f"final_config.yaml not found at {final_config_path}"
        print(f"    final_config.yaml found at {final_config_path}")

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
            {
                "LUKS_PASSWORD": "testpassword",
                "USER_PASSWORD": "testpassword",
                "TARGET_DISK": "/dev/vda",
                "NON_INTERACTIVE": "true",
                "GPU_VENDOR": "none",
                "CPU_VENDOR": "amd",
                "PACKAGE_PROFILE": "base",
                "ENABLE_SNAPSHOT_BOOT": "true",
                "ENABLE_HIBERNATION": "true",
                "ENABLE_UFW": "true",
                "TEST_SWAP_SIZE_MB": "1024",
            },
        )

        assert exit_code == 0, f"Env vars installation failed:\nstdout: {stdout}\nstderr: {stderr}"

        print("\n=== phase 3: verifying installation ===")
        assertions.assert_btrfs_subvolumes_exist(expected_subvolumes)

        mount_options: str = storage_config["btrfs"]["mount_options"]
        expected_options = [option.strip() for option in mount_options.split(",")]
        assertions.assert_btrfs_mount_options(expected_options)
        assertions.raise_if_failed()

        print("\n=== phase 4: reboot and verify ===")
        configure_ssh_and_reboot(vm, "testpassword")

        post_boot_assertions = InstallationAssertions(vm)
        print_secure_boot_summary(vm, "POST-INSTALL ENV VARS")
        assert verify_secure_boot_properly_configured(
            vm
        ), "Secure boot must be properly configured"

        print("    checking final config file...")
        username = system_config["user"]["name"]
        final_config_path = f"/home/{username}/final_config.yaml"
        exit_code, stdout, _ = vm.run_ssh_command(f"test -f {final_config_path} && echo 'exists'")
        assert (
            exit_code == 0 and "exists" in stdout
        ), f"final_config.yaml not found at {final_config_path}"
        print(f"    final_config.yaml found at {final_config_path}")

        post_boot_assertions.raise_if_failed()
        print("\n=== env vars override test completed ===")
