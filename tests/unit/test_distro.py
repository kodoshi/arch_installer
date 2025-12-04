import pytest

from arch_installer.core.distro import ArchBootstrapStrategy, MkinitcpioGenerator


class TestArchBootstrapStrategy:
    @pytest.fixture
    def strategy(self, fake_runner):
        return ArchBootstrapStrategy(fake_runner)

    def test_should_call_pacstrap_when_bootstrapping(self, strategy, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        strategy.bootstrap("/mnt", ["base", "linux"])

        fake_runner.assert_command_called("pacstrap")

    def test_should_include_packages_when_bootstrapping(self, strategy, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        strategy.bootstrap("/mnt", ["base", "linux", "btrfs-progs"])

        commands = fake_runner.get_commands()
        pacstrap_cmd = [c for c in commands if "pacstrap" in c][0]
        assert "base" in pacstrap_cmd
        assert "linux" in pacstrap_cmd
        assert "btrfs-progs" in pacstrap_cmd

    def test_should_use_target_path_when_bootstrapping(self, strategy, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        strategy.bootstrap("/custom/target", ["base"])

        commands = fake_runner.get_commands()
        pacstrap_cmd = [command for command in commands if "pacstrap" in command][0]
        assert "/custom/target" in pacstrap_cmd

    def test_should_call_genfstab_when_configuring_fstab(self, strategy, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        strategy.configure_fstab("/mnt")

        fake_runner.assert_command_called("genfstab")


class TestMkinitcpioGenerator:
    @pytest.fixture
    def generator(self, fake_runner):
        return MkinitcpioGenerator(fake_runner)

    def test_should_write_mkinitcpio_conf_when_configuring(self, generator, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        generator.configure(["systemd", "autodetect", "block", "filesystems"])

        commands = fake_runner.get_commands()
        assert len(commands) > 0

    def test_should_include_hooks_when_configuring(self, generator, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        generator.configure(["systemd", "sd-encrypt"])

        commands = fake_runner.get_commands()
        cat_cmd = [command for command in commands if "cat" in command][0]
        assert "HOOKS=" in cat_cmd
        assert "systemd" in cat_cmd
        assert "sd-encrypt" in cat_cmd

    def test_should_call_mkinitcpio_with_preset_when_generating_single(
        self, generator, fake_runner
    ):
        fake_runner.set_default_response(exit_code=0)

        generator.generate("linux")

        fake_runner.assert_command_called("mkinitcpio -p linux")
