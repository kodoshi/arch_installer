from arch_installer.config.models import FirewallConfig
from arch_installer.core.command import CommandRunner


class FirewallSetup:
    """configure UFW firewall with secure defaults."""

    def __init__(
        self,
        runner: CommandRunner,
        config: FirewallConfig,
        *,
        enabled: bool = True,
    ) -> None:
        self._runner = runner
        self._config = config
        self._enabled = enabled and config.enabled

    def configure_firewall(self) -> None:
        if not self._enabled:
            print(">>>>> Firewall disabled, skipping.")
            return

        print(">>>>> Configuring UFW firewall...")

        self._ensure_ufw_installed()
        self._set_default_policies()
        if self._config.logging:
            self._enable_logging()
        if self._config.block_icmp:
            self._block_icmp()
        if self._config.ssh.enabled:
            self._configure_ssh()
        self._apply_allow_rules()
        self._enable_ufw()

        print(">>>>> UFW firewall configured.")

    def _ensure_ufw_installed(self) -> None:
        result = self._runner.run_as_chroot("pacman -Q ufw", raise_on_nonzero_exit=False)
        if not result.success:
            print("    Installing ufw...")
            self._runner.run_as_chroot("pacman -S --noconfirm ufw")

    def _set_default_policies(self) -> None:
        print("    Setting default policies...")
        self._runner.run_as_chroot(f"ufw default {self._config.default_incoming} incoming")
        self._runner.run_as_chroot(f"ufw default {self._config.default_outgoing} outgoing")

    def _enable_logging(self) -> None:
        print("    Enabling logging...")
        self._runner.run_as_chroot("ufw logging on")

    def _block_icmp(self) -> None:
        print("    Blocking ICMP (ping)...")

        before_rules = "/mnt/etc/ufw/before.rules"
        result = self._runner.run(
            f"grep -q 'block icmp' {before_rules}", raise_on_nonzero_exit=False
        )
        if result.success:
            return

        icmp_rules = [
            "-A ufw-before-input -p icmp --icmp-type destination-unreachable -j ACCEPT",
            "-A ufw-before-input -p icmp --icmp-type time-exceeded -j ACCEPT",
            "-A ufw-before-input -p icmp --icmp-type parameter-problem -j ACCEPT",
            "-A ufw-before-input -p icmp --icmp-type echo-request -j ACCEPT",
        ]

        for rule in icmp_rules:
            escaped = rule.replace("/", "\\/").replace(".", "\\.").replace("-", "\\-")
            self._runner.run(f"sed -i '/{escaped}/d' {before_rules}", raise_on_nonzero_exit=False)

    def _configure_ssh(self) -> None:
        ssh_config = self._config.ssh
        print(f"    Allowing SSH on port {ssh_config.port}...")

        if ssh_config.allowed_from:
            self._runner.run_as_chroot(
                f"ufw allow from {ssh_config.allowed_from} to any port {ssh_config.port} proto tcp"
            )
        else:
            self._runner.run_as_chroot(f"ufw allow {ssh_config.port}/tcp")

        self._runner.run_as_chroot("systemctl enable sshd.service")

    def _apply_allow_rules(self) -> None:
        for rule in self._config.allow_rules:
            print(f"    Allowing port {rule.port}/{rule.protocol}...")
            self._runner.run_as_chroot(f"ufw allow {rule.port}/{rule.protocol}")

    def _enable_ufw(self) -> None:
        print("    Enabling UFW...")
        # set ufw to start on boot and enable it
        self._runner.run_as_chroot("systemctl enable ufw.service")
        # enable ufw firewall with --force to skip interactive prompt
        self._runner.run_as_chroot("ufw --force enable", raise_on_nonzero_exit=False)
