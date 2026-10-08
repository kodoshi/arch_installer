from dataclasses import replace

from arch_installer.config.models import (
    SwapConfig,
    UkiVariantConfig,
    UsbBootConfig,
)
from arch_installer.executors.boot import (
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
        cmdline = kernel_cmdline(build_config(), "uuid-1234", None)
        assert "rd.luks.name=uuid-1234=cryptroot" in cmdline
        assert "root=/dev/mapper/cryptroot" in cmdline

    def test_includes_quiet_and_hardening_options(self):
        cmdline = kernel_cmdline(build_config(), "uuid-1234", None)
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
        cmdline = kernel_cmdline(config, "uuid-1234", None)
        assert "lockdown=" not in cmdline
        assert "iommu=force" in cmdline

    def test_detached_header_adds_the_usb_header_option(self):
        config = build_config(
            usb_boot=UsbBootConfig(enabled=True, device="/dev/sdb", detached_luks_header=True)
        )
        cmdline = kernel_cmdline(config, "uuid-1234", None)
        assert "header=/luks_header.img:LABEL=LUKSHEADER" in cmdline

    def test_resume_is_added_only_with_hibernation(self):
        swap_on = SwapConfig(enabled=True, size_mb=1024, hibernation=True)
        config = build_config(storage=replace(build_config().storage, swap=swap_on))
        cmdline = kernel_cmdline(config, "uuid-1234", "98765")
        assert "resume=/dev/mapper/cryptroot" in cmdline
        assert "resume_offset=98765" in cmdline

    def test_no_resume_without_hibernation(self):
        cmdline = kernel_cmdline(build_config(), "uuid-1234", None)
        assert "resume=" not in cmdline


class TestUkiVariants:
    def test_default_variant_always_present(self):
        variants = uki_variants(build_config())
        assert any(variant.suffix == "default" for variant in variants)

    def test_configured_variants_are_added(self):
        config = build_config()
        config = replace(
            config, boot=replace(config.boot, variants=(UkiVariantConfig("no-dc", "amdgpu.dc=0"),))
        )
        suffixes = [variant.suffix for variant in uki_variants(config)]
        assert suffixes == ["default", "no-dc"]

    def test_uki_path_encodes_kernel_and_variant(self):
        path = uki_path("linux", UkiVariantConfig("default"))
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
        preset = kernel_preset("linux", (UkiVariantConfig("default"),))
        assert "arch-linux-default.efi" in preset
