import pytest

from arch_installer.config.models import (
    FirewallAllowRule,
    FirewallConfig,
    FirewallSshConfig,
)
from arch_installer.steps.firewall import FirewallSetup


def create_firewall_config(
    enabled: bool = True,
    default_incoming: str = "deny",
    default_outgoing: str = "allow",
    logging: bool = True,
    block_icmp: bool = True,
    ssh_enabled: bool = False,
    ssh_port: int = 22,
    ssh_allowed_from: str | None = None,
    allow_rules: tuple[FirewallAllowRule, ...] = (),
) -> FirewallConfig:
    return FirewallConfig(
        enabled=enabled,
        default_incoming=default_incoming,
        default_outgoing=default_outgoing,
        logging=logging,
        block_icmp=block_icmp,
        ssh=FirewallSshConfig(
            enabled=ssh_enabled,
            port=ssh_port,
            allowed_from=ssh_allowed_from,
        ),
        allow_rules=allow_rules,
    )


class TestFirewallSetup:
    @pytest.fixture
    def config(self):
        return create_firewall_config()

    @pytest.fixture
    def setup(self, fake_runner, config):
        return FirewallSetup(fake_runner, config, enabled=True)

    def test_should_enable_ufw_when_configured(self, setup, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        setup.configure_firewall()

        fake_runner.assert_command_called("ufw")

    def test_should_deny_incoming_by_default_when_configured(self, setup, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        deny_incoming = any("default deny" in command for command in ufw_commands)
        assert deny_incoming

    def test_should_allow_outgoing_by_default_when_configured(self, setup, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        allow_outgoing = any("default allow" in command for command in ufw_commands)
        assert allow_outgoing

    def test_should_enable_ufw_service_when_configured(self, setup, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        setup.configure_firewall()

        systemctl_commands = fake_runner.get_commands("systemctl")
        enable_ufw = any(
            "enable" in command and "ufw" in command for command in systemctl_commands
        )
        assert enable_ufw

    def test_should_not_allow_ssh_when_ssh_disabled(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        config = create_firewall_config(ssh_enabled=False)

        setup = FirewallSetup(fake_runner, config, enabled=True)
        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        ssh_commands = [c for c in ufw_commands if "allow" in c and "22" in c]
        assert len(ssh_commands) == 0

    def test_should_allow_ssh_when_ssh_enabled(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        config = create_firewall_config(ssh_enabled=True, ssh_port=22)

        setup = FirewallSetup(fake_runner, config, enabled=True)
        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        ssh_commands = [c for c in ufw_commands if "allow" in c and "22" in c]
        assert len(ssh_commands) == 1

    def test_should_allow_ssh_from_specific_subnet_when_configured(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        config = create_firewall_config(
            ssh_enabled=True,
            ssh_port=2222,
            ssh_allowed_from="192.168.1.0/24",
        )

        setup = FirewallSetup(fake_runner, config, enabled=True)
        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        ssh_commands = [c for c in ufw_commands if "allow" in c and "192.168.1.0/24" in c]
        assert len(ssh_commands) == 1
        assert "2222" in ssh_commands[0]

    def test_should_enable_logging_when_configured(self, setup, fake_runner):
        fake_runner.set_default_response(exit_code=0)

        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        logging_enabled = any("logging" in command for command in ufw_commands)
        assert logging_enabled

    def test_should_skip_logging_when_logging_disabled(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        config = create_firewall_config(logging=False)

        setup = FirewallSetup(fake_runner, config, enabled=True)
        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        logging_enabled = any("logging" in command for command in ufw_commands)
        assert not logging_enabled

    def test_should_skip_firewall_when_disabled(self, fake_runner):
        config = create_firewall_config(enabled=False)

        setup = FirewallSetup(fake_runner, config, enabled=True)
        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        assert len(ufw_commands) == 0

    def test_should_apply_custom_allow_rules(self, fake_runner):
        fake_runner.set_default_response(exit_code=0)
        config = create_firewall_config(
            allow_rules=(
                FirewallAllowRule(port=80, protocol="tcp"),
                FirewallAllowRule(port=443, protocol="tcp"),
            )
        )

        setup = FirewallSetup(fake_runner, config, enabled=True)
        setup.configure_firewall()

        ufw_commands = fake_runner.get_commands("ufw")
        port_80_allowed = any("allow" in c and "80/tcp" in c for c in ufw_commands)
        port_443_allowed = any("allow" in c and "443/tcp" in c for c in ufw_commands)
        assert port_80_allowed
        assert port_443_allowed
