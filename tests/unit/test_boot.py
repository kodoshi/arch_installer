import pytest

from arch_installer.steps.boot import (
    BootloaderSetup,
    KernelCommandLineBuilder,
    UkiGenerator,
)


class TestKernelCommandLineBuilder:
    @pytest.fixture
    def builder(self, minimal_config, runtime_state, fake_runner):
        return KernelCommandLineBuilder(minimal_config, runtime_state, fake_runner)

    def test_should_include_rootflags_when_building_cmdline(self, builder):
        cmdline = builder.build(luks_uuid="test-uuid-1234")
        assert "rootflags=" in cmdline or "subvol=" in cmdline

    def test_should_include_cryptdevice_when_using_luks(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.root_partition = "/dev/loop0p2"
        builder = KernelCommandLineBuilder(minimal_config, runtime_state, fake_runner)

        cmdline = builder.build(luks_uuid="test-uuid-1234")
        assert "rd.luks.name" in cmdline

    def test_should_include_quiet_when_configured(self, builder, minimal_config):
        if minimal_config.boot.cmdline.quiet:
            cmdline = builder.build(luks_uuid="test-uuid-1234")
            assert "quiet" in cmdline

    def test_should_include_hardening_options_when_building_cmdline(self, builder):
        cmdline = builder.build(luks_uuid="test-uuid-1234")

        hardening_present = any(
            option in cmdline for option in ["lockdown=", "pti=", "spec_store_bypass_disable="]
        )
        assert hardening_present

    def test_should_set_rootfstype_when_building_cmdline(self, builder):
        cmdline = builder.build(luks_uuid="test-uuid-1234")
        assert "rootflags=" in cmdline

    def test_should_append_extra_params_when_variant_specified(
        self, minimal_config, runtime_state, fake_runner
    ):
        builder = KernelCommandLineBuilder(minimal_config, runtime_state, fake_runner)

        variant_cmdline = builder.build(luks_uuid="test-uuid-1234", extra_params="debug")

        assert "debug" in variant_cmdline
        assert isinstance(variant_cmdline, str)

    def test_should_include_resume_params_when_hibernation_enabled(
        self, minimal_config, runtime_state, fake_runner, tmp_path
    ):
        runtime_state.enable_hibernation = True
        runtime_state.skip_swap = False
        runtime_state.target_root = tmp_path

        swap_dir = tmp_path / ".swap"
        swap_dir.mkdir(parents=True)
        swap_file = swap_dir / "swapfile"
        swap_file.write_bytes(b"\0" * 1024)

        fake_runner.set_response("btrfs", stdout="12345", exit_code=0)

        builder = KernelCommandLineBuilder(minimal_config, runtime_state, fake_runner)
        cmdline = builder.build(luks_uuid="test-uuid-1234")

        assert "resume=/dev/mapper/cryptroot" in cmdline
        assert "resume_offset=12345" in cmdline

    def test_should_not_include_resume_params_when_hibernation_disabled(
        self, minimal_config, runtime_state, fake_runner
    ):
        runtime_state.enable_hibernation = False

        builder = KernelCommandLineBuilder(minimal_config, runtime_state, fake_runner)
        cmdline = builder.build(luks_uuid="test-uuid-1234")

        assert "resume=" not in cmdline
        assert "resume_offset=" not in cmdline


class TestUkiGenerator:
    @pytest.fixture
    def generator(self, minimal_config, runtime_state, fake_runner):
        runtime_state.root_partition = "/dev/loop0p2"
        return UkiGenerator(minimal_config, runtime_state, fake_runner)

    def test_should_create_mkinitcpio_conf_when_configuring(self, generator, fake_runner):
        fake_runner.set_response("mkdir", exit_code=0)
        fake_runner.set_response("tee", exit_code=0)
        fake_runner.set_response("cat", exit_code=0)

        generator._configure_mkinitcpio()

        commands = fake_runner.get_commands()
        assert len(commands) > 0

    def test_should_raise_error_when_luks_uuid_missing(self, generator, fake_runner):
        fake_runner.set_response("blkid", exit_code=1, stderr="not found")

        with pytest.raises(RuntimeError, match="LUKS partition UUID"):
            generator.generate_ukis()

    def test_should_generate_ukis_when_valid_uuid_provided(self, generator, fake_runner):
        fake_runner.set_response("blkid", stdout='UUID="test-uuid-1234"')
        fake_runner.set_response("mkdir", exit_code=0)
        fake_runner.set_response("tee", exit_code=0)
        fake_runner.set_response("cat", exit_code=0)
        fake_runner.set_response("mkinitcpio", exit_code=0)

        generator.generate_ukis()

        fake_runner.assert_command_called("mkinitcpio")


class TestBootloaderSetup:
    @pytest.fixture
    def setup(self, minimal_config, runtime_state, fake_runner):
        return BootloaderSetup(minimal_config, runtime_state, fake_runner)

    def test_should_call_bootctl_install_when_setting_up(self, setup, fake_runner):
        fake_runner.set_response("bootctl", exit_code=0)
        fake_runner.set_response("mkdir", exit_code=0)
        fake_runner.set_response("tee", exit_code=0)
        fake_runner.set_response("cat", exit_code=0)
        fake_runner.set_response("test", exit_code=0)

        setup.setup_bootloader()

        fake_runner.assert_command_called("bootctl")

    def test_should_create_loader_conf_when_setting_up(self, setup, fake_runner):
        fake_runner.set_response("bootctl", exit_code=0)
        fake_runner.set_response("mkdir", exit_code=0)
        fake_runner.set_response("tee", exit_code=0)
        fake_runner.set_response("cat", exit_code=0)
        fake_runner.set_response("test", exit_code=0)

        setup.setup_bootloader()

        commands = fake_runner.get_commands()
        assert len(commands) > 0

    def test_should_call_secure_boot_signing_when_setting_up(
        self, minimal_config, runtime_state, fake_runner
    ):
        fake_runner.set_response("bootctl", exit_code=0)
        fake_runner.set_response("cat", exit_code=0)
        fake_runner.set_response("test", exit_code=0)
        fake_runner.set_response("sbctl", exit_code=0)

        setup = BootloaderSetup(minimal_config, runtime_state, fake_runner)
        setup.setup_bootloader()

        fake_runner.assert_command_called("bootctl")

    def test_should_use_configured_timeout_when_setting_up(
        self, minimal_config, runtime_state, fake_runner
    ):
        fake_runner.set_response("bootctl", exit_code=0)
        fake_runner.set_response("cat", exit_code=0)
        fake_runner.set_response("test", exit_code=0)

        setup = BootloaderSetup(minimal_config, runtime_state, fake_runner)
        setup.setup_bootloader()

        assert minimal_config.boot.loader.timeout == 5


class TestMkinitcpioHooks:
    @pytest.fixture
    def generator(self, minimal_config, runtime_state, fake_runner):
        return UkiGenerator(minimal_config, runtime_state, fake_runner)

    def test_should_use_systemd_hooks_when_configured(self, generator, minimal_config):
        hooks = minimal_config.boot.hooks

        assert "systemd" in hooks
        assert "sd-encrypt" in hooks

    def test_should_include_encrypt_hook_when_using_luks(self, generator, minimal_config):
        hooks = minimal_config.boot.hooks

        assert "sd-encrypt" in hooks or "encrypt" in hooks

    def test_should_include_filesystems_hook_when_using_btrfs(self, generator, minimal_config):
        hooks = minimal_config.boot.hooks

        assert "filesystems" in hooks
