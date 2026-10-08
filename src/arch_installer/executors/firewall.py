"""UFW firewall, configured offline and enabled on the first boot of the installed system."""

import logging

from arch_installer.executors.base import TARGET_ROOT, Executor

logger = logging.getLogger(__name__)

UFW_CONF = f"{TARGET_ROOT}/etc/ufw/ufw.conf"
UFW_BEFORE_RULES = f"{TARGET_ROOT}/etc/ufw/before.rules"

# the ACCEPT rules ufw ships for inbound ICMP; deleting them makes the default policy drop pings
INBOUND_ICMP_ACCEPT_RULES = (
    "-A ufw-before-input -p icmp --icmp-type destination-unreachable -j ACCEPT",
    "-A ufw-before-input -p icmp --icmp-type time-exceeded -j ACCEPT",
    "-A ufw-before-input -p icmp --icmp-type parameter-problem -j ACCEPT",
    "-A ufw-before-input -p icmp --icmp-type echo-request -j ACCEPT",
)


class FirewallExecutor(Executor):
    def execute(self) -> None:
        firewall = self._config.firewall
        logger.info(
            "Configuring UFW (incoming %s, outgoing %s)...",
            firewall.default_incoming,
            firewall.default_outgoing,
        )

        if not self._runner.run_as_chroot("pacman -Q ufw", raise_on_nonzero_exit=False).success:
            self._runner.run_as_chroot("pacman -S --noconfirm ufw")

        # ufw pushes rule changes into the running kernel whenever ufw.conf says it is
        # enabled, and inside arch-chroot that kernel is the live ISO's. keeping the flag
        # off while configuring makes every ufw call below a pure config-file edit
        self._set_enabled_on_boot(False)

        self._runner.run_as_chroot(f"ufw default {firewall.default_incoming} incoming")
        self._runner.run_as_chroot(f"ufw default {firewall.default_outgoing} outgoing")
        if firewall.logging:
            self._runner.run_as_chroot("ufw logging on")
        if firewall.block_icmp:
            for rule in INBOUND_ICMP_ACCEPT_RULES:
                escaped_rule = rule.replace("/", "\\/").replace(".", "\\.").replace("-", "\\-")
                self._runner.run(
                    f"sed -i '/{escaped_rule}/d' {UFW_BEFORE_RULES}", raise_on_nonzero_exit=False
                )
        if firewall.ssh.enabled:
            self._allow_ssh()
        for rule in firewall.allow_rules:
            self._runner.run_as_chroot(f"ufw allow {rule.port}/{rule.protocol}")

        self._runner.run_as_chroot("systemctl enable ufw.service")
        # never `ufw enable` here: it would firewall the live ISO, not the target
        self._set_enabled_on_boot(True)

    def _allow_ssh(self) -> None:
        ssh = self._config.firewall.ssh
        if ssh.allowed_from:
            self._runner.run_as_chroot(
                f"ufw allow from {ssh.allowed_from} to any port {ssh.port} proto tcp"
            )
        else:
            self._runner.run_as_chroot(f"ufw allow {ssh.port}/tcp")
        self._runner.run_as_chroot("systemctl enable sshd.service")

    def _set_enabled_on_boot(self, enabled: bool) -> None:
        flag = "yes" if enabled else "no"
        self._runner.run(f"sed -i 's/^ENABLED=.*/ENABLED={flag}/' {UFW_CONF}")
