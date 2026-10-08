from dataclasses import replace

import pytest

from arch_installer.config.models import UkiVariantConfig
from arch_installer.executors.boot import (
    USB_STORAGE_INITRAMFS_MODULES,
    BootloaderStepExecutor,
    DetachedLuksHeader,
    KernelImagesStepExecutor,
    kernel_cmdline,
    kernel_preset,
    loader_conf,
    mkinitcpio_conf,
    uki_path,
    uki_variants,
)
from tests.unit.conftest import build_config


class TestKernelCmdline:
    def test_names_the_luks_mapping_and_encrypted_root(self):
        cmdline = kernel_cmdline(build_config(), "uuid-1234", None, None)
        assert "rd.luks.name=uuid-1234=cryptroot" in cmdline
        assert "root=/dev/mapper/cryptroot" in cmdline

    def test_includes_quiet_and_hardening_options(self):
        cmdline = kernel_cmdline(build_config(), "uuid-1234", None, None)
        assert "quiet" in cmdline
        assert "lockdown=integrity" in cmdline
        assert "pti=on" in cmdline

    def test_omits_hardening_option_left_empty(self):
        config = build_config()
        hardening = replace(config.boot.cmdline.hardening, lockdown="", iommu="")
        config = replace(
            config,
            boot=replace(config.boot, cmdline=replace(config.boot.cmdline, hardening=hardening)),
        )
        cmdline = kernel_cmdline(config, "uuid-1234", None, None)
        assert "lockdown=" not in cmdline
        assert "iommu=force" in cmdline

    def test_detached_header_names_the_disk_and_the_header_partition(self):
        detached_header = DetachedLuksHeader(
            encrypted_device_path="/dev/disk/by-id/wwn-0x5000", header_partition_uuid="2222-header"
        )
        cmdline = kernel_cmdline(build_config(), "uuid-1234", detached_header, None)
        assert "rd.luks.name=uuid-1234=cryptroot" in cmdline
        assert "rd.luks.data=uuid-1234=/dev/disk/by-id/wwn-0x5000" in cmdline
        assert "rd.luks.options=uuid-1234=header=/dev/disk/by-partuuid/2222-header" in cmdline

    def test_resume_is_added_only_with_hibernation(self):
        swap_on = replace(build_config().storage.swap, enabled=True, size_mb=1024, hibernation=True)
        config = build_config(storage=replace(build_config().storage, swap=swap_on))
        cmdline = kernel_cmdline(config, "uuid-1234", None, "98765")
        assert "resume=/dev/mapper/cryptroot" in cmdline
        assert "resume_offset=98765" in cmdline

    def test_no_resume_without_hibernation(self):
        cmdline = kernel_cmdline(build_config(), "uuid-1234", None, None)
        assert "resume=" not in cmdline


class TestUkiVariants:
    def test_default_variant_always_present(self):
        variants = uki_variants(build_config())
        assert any(variant.suffix == "default" for variant in variants)

    def test_configured_variants_are_added(self):
        config = build_config()
        config = replace(
            config,
            boot=replace(
                config.boot, variants=(UkiVariantConfig(suffix="no-dc", params="amdgpu.dc=0"),)
            ),
        )
        suffixes = [variant.suffix for variant in uki_variants(config)]
        assert suffixes == ["default", "no-dc"]

    def test_uki_path_encodes_kernel_and_variant(self):
        path = uki_path("linux", UkiVariantConfig(suffix="default", params=""))
        assert path.endswith("arch-linux-default.efi")


class TestBootTemplates:
    def test_mkinitcpio_conf_lists_hooks_and_modules(self):
        rendered_file = mkinitcpio_conf(("base", "systemd"), ("nvidia",))
        assert "HOOKS=(base systemd)" in rendered_file
        assert "MODULES=(nvidia)" in rendered_file

    def test_loader_conf_renders_timeout_and_editor(self):
        rendered_file = loader_conf(build_config().boot.loader)
        assert "timeout 20" in rendered_file
        assert "editor no" in rendered_file

    def test_kernel_preset_references_the_uki_path(self):
        preset = kernel_preset("linux", (UkiVariantConfig(suffix="default", params=""),))
        assert "arch-linux-default.efi" in preset


def usb_boot_config():
    base = build_config()
    return build_config(
        storage=replace(base.storage, target_disk="/dev/vda"),
        usb_boot=replace(base.usb_boot, enabled=True, device="/dev/sdb"),
    )


def installed_kernel(fake_runner):
    fake_runner.set_default_response(exit_code=0)
    fake_runner.set_response("cryptsetup luksUUID /dev/sdb2", stdout="uuid-1234\n")
    fake_runner.set_response("/dev/disk/by-id/*", stdout="/dev/disk/by-id/virtio-dali-disk\n")
    fake_runner.set_response("blkid -s PARTUUID -o value /dev/sdb2", stdout="2222-header\n")
    fake_runner.set_response("sbctl status", stdout="Setup Mode: Disabled\n")
    return fake_runner


class TestKernelImagesWithUsbBootDrive:
    def test_reads_the_luks_uuid_from_the_header_on_the_drive(self, fake_runner):
        KernelImagesStepExecutor(usb_boot_config(), installed_kernel(fake_runner)).execute()

        cmdline = fake_runner.written_content("/mnt/etc/kernel/cmdline")
        assert "rd.luks.data=uuid-1234=/dev/disk/by-id/virtio-dali-disk" in cmdline
        assert "header=/dev/disk/by-partuuid/2222-header" in cmdline

    def test_refuses_a_disk_without_a_by_id_name(self, fake_runner):
        installed_kernel(fake_runner)
        fake_runner.set_response("/dev/disk/by-id/*", stdout="")

        with pytest.raises(RuntimeError, match="no /dev/disk/by-id name"):
            KernelImagesStepExecutor(usb_boot_config(), fake_runner).execute()

    def test_initramfs_reaches_usb_storage(self, fake_runner):
        KernelImagesStepExecutor(usb_boot_config(), installed_kernel(fake_runner)).execute()

        mkinitcpio_conf_file = fake_runner.written_content("/mnt/etc/mkinitcpio.conf")
        assert " ".join(USB_STORAGE_INITRAMFS_MODULES) in mkinitcpio_conf_file


class TestBootloaderOnUsbBootDrive:
    def test_leaves_no_trace_in_the_firmware_variables(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        BootloaderStepExecutor(usb_boot_config(), fake_runner).execute()

        fake_runner.assert_command_called(
            "bootctl install --esp-path=/efi --variables=no --random-seed=no"
        )
        fake_runner.assert_command_called("systemctl mask systemd-boot-random-seed.service")

    def test_internal_disk_install_registers_the_boot_entry(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        BootloaderStepExecutor(build_config(), fake_runner).execute()

        assert any(
            command.endswith("bootctl install --esp-path=/efi")
            for command in fake_runner.get_commands("bootctl install")
        )
