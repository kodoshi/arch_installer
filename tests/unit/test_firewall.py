from arch_installer.config.models import (
    FirewallAllowRule,
    FirewallConfig,
    FirewallPolicy,
    FirewallSshConfig,
)
from arch_installer.executors.firewall import FirewallExecutor
from tests.unit.conftest import build_config


def run_firewall(fake_runner, firewall: FirewallConfig):
    fake_runner.set_default_response(exit_code=0)
    FirewallExecutor(build_config(firewall=firewall), fake_runner).execute()


class TestFirewallExecutor:
    def test_sets_default_policies(self, fake_runner):
        run_firewall(fake_runner, FirewallConfig())
        ufw = fake_runner.get_commands("ufw default")
        assert any("deny incoming" in command for command in ufw)
        assert any("allow outgoing" in command for command in ufw)

    def test_enables_the_service_without_activating_ufw_in_the_chroot(self, fake_runner):
        run_firewall(fake_runner, FirewallConfig())
        # enabling the service is correct, running `ufw enable` would firewall the live ISO
        fake_runner.assert_command_called("systemctl enable ufw.service")
        assert not any(command.endswith("ufw enable") for command in fake_runner.get_commands())

    def test_configures_rules_with_the_enabled_flag_off(self, fake_runner):
        run_firewall(fake_runner, FirewallConfig())
        # the flag is flipped off before writing rules and back on at the very end
        enabled_edits = fake_runner.get_commands("ENABLED=")
        assert "ENABLED=no" in enabled_edits[0]
        assert "ENABLED=yes" in enabled_edits[-1]

    def test_opens_ssh_when_enabled(self, fake_runner):
        run_firewall(fake_runner, FirewallConfig(ssh=FirewallSshConfig(enabled=True, port=2222)))
        assert any("allow 2222/tcp" in command for command in fake_runner.get_commands("ufw"))
        fake_runner.assert_command_called("systemctl enable sshd.service")

    def test_restricts_ssh_to_an_allowed_source(self, fake_runner):
        run_firewall(
            fake_runner,
            FirewallConfig(ssh=FirewallSshConfig(enabled=True, allowed_from="192.168.1.0/24")),
        )
        assert any("from 192.168.1.0/24" in command for command in fake_runner.get_commands("ufw"))

    def test_opens_additional_allow_rules(self, fake_runner):
        run_firewall(
            fake_runner,
            FirewallConfig(allow_rules=(FirewallAllowRule(port=443, protocol="tcp"),)),
        )
        assert any("allow 443/tcp" in command for command in fake_runner.get_commands("ufw"))

    def test_reject_policy_is_passed_through(self, fake_runner):
        run_firewall(fake_runner, FirewallConfig(default_incoming=FirewallPolicy.REJECT))
        assert any(
            "default reject incoming" in command for command in fake_runner.get_commands("ufw")
        )
