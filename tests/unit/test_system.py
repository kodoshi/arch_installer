import pytest

from arch_installer.steps.system import SystemConfigurator


class TestSystemConfigurator:
    @pytest.fixture
    def configurator(self, minimal_config, runtime_state, fake_runner):
        return SystemConfigurator(minimal_config, runtime_state, fake_runner)

    def test_should_set_timezone_when_configuring_timezone(self, configurator, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        configurator._configure_timezone()

        fake_runner.assert_command_called("ln")
        fake_runner.assert_command_called("hwclock")

    def test_should_generate_locale_when_configuring_locale(self, configurator, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        configurator._configure_locale()

        fake_runner.assert_command_called("locale-gen")

    def test_should_generate_all_distinct_locales_when_configured(self, configurator):
        from arch_installer.config.models import LocaleConfig

        locale_config = LocaleConfig(
            language="en_US",
            encoding="UTF-8",
            keymap="us",
            monetary="fr_FR.UTF-8",
            time_format="fr_FR.UTF-8",
            numeric="en_US.UTF-8",
            paper="fr_FR.UTF-8",
        )

        locales = configurator._collect_distinct_locales(locale_config)

        assert "en_US.UTF-8" in locales
        assert "fr_FR.UTF-8" in locales
        assert len(locales) == 2

    def test_should_set_password_when_creating_user(self, configurator, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("id", exit_code=1)

        configurator._create_user()

        fake_runner.assert_command_called("chpasswd")


class TestHostsFile:
    """Tests for /etc/hosts configuration."""

    @pytest.fixture
    def configurator(self, minimal_config, runtime_state, fake_runner):
        """Create system configurator."""
        return SystemConfigurator(minimal_config, runtime_state, fake_runner)

    def test_should_set_hostname_when_configuring_hostname(self, configurator, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        configurator._configure_hostname()

        commands = fake_runner.get_commands()
        assert len(commands) > 0


class TestSudoConfiguration:
    """Tests for sudo configuration."""

    @pytest.fixture
    def configurator(self, minimal_config, runtime_state, fake_runner):
        """Create system configurator."""
        return SystemConfigurator(minimal_config, runtime_state, fake_runner)

    def test_should_configure_wheel_group_when_creating_user(self, configurator, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        fake_runner.set_response("id", exit_code=1)

        configurator._create_user()

        sed_cmds = fake_runner.get_commands("sed")
        assert len(sed_cmds) > 0
