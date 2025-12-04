from dataclasses import replace

import pytest

from arch_installer.steps.snapper import SnapperSetup


class TestSnapperSetup:
    @pytest.fixture
    def setup(self, minimal_config, runtime_state, fake_runner):
        return SnapperSetup(minimal_config, runtime_state, fake_runner)

    def test_should_skip_configuration_when_snapper_disabled(
        self, minimal_config, runtime_state, fake_runner
    ):
        disabled_snapper = replace(minimal_config.snapper, enabled=False)
        config_with_disabled_snapper = replace(minimal_config, snapper=disabled_snapper)
        setup = SnapperSetup(config_with_disabled_snapper, runtime_state, fake_runner)

        setup.configure_snapper()

        assert len(fake_runner.get_commands()) == 0

    def test_should_check_snapper_installed_when_enabled(
        self, minimal_config, runtime_state, fake_runner
    ):
        setup = SnapperSetup(minimal_config, runtime_state, fake_runner)

        setup.configure_snapper()

        fake_runner.assert_command_called("pacman -Q snapper")

    def test_should_install_snapper_when_not_already_installed(
        self, minimal_config, runtime_state, fake_runner
    ):
        fake_runner.set_response("pacman -Q snapper", exit_code=1)
        setup = SnapperSetup(minimal_config, runtime_state, fake_runner)

        setup.configure_snapper()

        fake_runner.assert_command_called("pacman -S --noconfirm snapper")

    def test_should_enable_timeline_timer_when_configured(
        self, minimal_config, runtime_state, fake_runner
    ):
        setup = SnapperSetup(minimal_config, runtime_state, fake_runner)

        setup.configure_snapper()

        fake_runner.assert_command_called("systemctl enable snapper-timeline.timer")

    def test_should_enable_cleanup_timer_when_configured(
        self, minimal_config, runtime_state, fake_runner
    ):
        setup = SnapperSetup(minimal_config, runtime_state, fake_runner)

        setup.configure_snapper()

        fake_runner.assert_command_called("systemctl enable snapper-cleanup.timer")

    def test_should_create_config_directories_when_configuring(
        self, minimal_config, runtime_state, fake_runner
    ):
        setup = SnapperSetup(minimal_config, runtime_state, fake_runner)

        setup.configure_snapper()

        fake_runner.assert_command_called("mkdir -p /mnt/etc/snapper/configs")
