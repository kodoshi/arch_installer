"""System configuration - hostname, timezone, locale, user creation."""

from arch_installer.config.models import DeclaredConfig
from arch_installer.core.command import CommandRunner
from arch_installer.core.runtime_state import RuntimeConfig
from arch_installer.templates.system import hosts_file


class SystemConfigurator:
    def __init__(
        self,
        config: DeclaredConfig,
        state: RuntimeConfig,
        runner: CommandRunner,
    ) -> None:
        self._config = config
        self._state = state
        self._runner = runner
        self._system_config = config.system

    def configure_system(self) -> None:
        print(">>>>> Converging system configuration...")

        self._configure_hostname()
        self._configure_timezone()
        self._configure_locale()
        self._configure_keymap()
        self._create_user()

        if self._config.docker.enabled:
            self._configure_docker()

        print(">>>>> System configuration complete.")

    def _configure_hostname(self) -> None:
        hostname = self._system_config.hostname
        print(f"    Setting hostname: {hostname}")

        self._runner.run(f'echo "{hostname}" > /mnt/etc/hostname')

        hosts_content = hosts_file(hostname)
        self._runner.run(f"cat > /mnt/etc/hosts << 'EOF'\n{hosts_content}EOF")

    def _configure_timezone(self) -> None:
        timezone = self._system_config.timezone
        print(f"    Setting timezone: {timezone}")

        self._runner.run("rm -f /mnt/etc/localtime")
        self._runner.run(f"ln -sf /usr/share/zoneinfo/{timezone} /mnt/etc/localtime")
        self._runner.run_as_chroot("hwclock --systohc", raise_on_nonzero_exit=False)

    def _configure_locale(self) -> None:
        locale_config = self._system_config.locale
        full_locale = locale_config.full_locale
        print(f"    Setting primary locale: {full_locale}")

        # collect all distinct locales that need to be generated
        locales_to_generate = self._collect_distinct_locales(locale_config)
        print(f"    Locales to generate: {locales_to_generate}")

        locale_gen = "/mnt/etc/locale.gen"
        for locale in locales_to_generate:
            self._runner.run(
                f"sed -i 's/^#\\s*\\({locale}\\s\\)/\\1/' {locale_gen}",
                raise_on_nonzero_exit=False,
            )

        self._runner.run_as_chroot("locale-gen", raise_on_nonzero_exit=False)

        # write locale.conf with all LC_* variables
        self._write_locale_conf(locale_config)

    def _collect_distinct_locales(self, locale_config) -> list[str]:
        full_locale = locale_config.full_locale
        locales = {full_locale}

        # add other locale settings if they differ from the main locale
        for locale_value in [
            locale_config.monetary,
            locale_config.time_format,
            locale_config.numeric,
            locale_config.paper,
        ]:
            if locale_value and locale_value != full_locale:
                locales.add(locale_value)

        return sorted(locales)

    def _write_locale_conf(self, locale_config) -> None:
        full_locale = locale_config.full_locale

        locale_conf_lines = [
            f"LANG={full_locale}",
        ]

        # add LC_* variables only if they differ from LANG
        if locale_config.monetary and locale_config.monetary != full_locale:
            locale_conf_lines.append(f"LC_MONETARY={locale_config.monetary}")

        if locale_config.time_format and locale_config.time_format != full_locale:
            locale_conf_lines.append(f"LC_TIME={locale_config.time_format}")

        if locale_config.numeric and locale_config.numeric != full_locale:
            locale_conf_lines.append(f"LC_NUMERIC={locale_config.numeric}")

        if locale_config.paper and locale_config.paper != full_locale:
            locale_conf_lines.append(f"LC_PAPER={locale_config.paper}")

        locale_conf_content = "\n".join(locale_conf_lines)
        self._runner.run(f'echo "{locale_conf_content}" > /mnt/etc/locale.conf')

    def _configure_keymap(self) -> None:
        keymap = self._system_config.locale.keymap
        print(f"    Setting keymap: {keymap}")

        self._runner.run(f'echo "KEYMAP={keymap}" > /mnt/etc/vconsole.conf')

    def _create_user(self) -> None:
        user = self._system_config.user
        username = user.name
        groups = ",".join(user.groups)

        print(f"    Creating user: {username}")

        result = self._runner.run_as_chroot(f"id {username}", raise_on_nonzero_exit=False)
        if result.success:
            print(f"    User {username} already exists")
        else:
            self._runner.run_as_chroot(f"useradd -m -G {groups} -s /bin/bash {username}")
            print(f"    User {username} created (groups: {groups})")

            self._runner.run(
                "sed -i 's/^#\\s*\\(%wheel ALL=(ALL:ALL) ALL\\)/\\1/' /mnt/etc/sudoers",
                raise_on_nonzero_exit=False,
            )

        if self._state.user_password:
            print(f"    Setting password for {username}...")
            self._runner.run_as_chroot(
                "chpasswd",
                input_data=f"{username}:{self._state.user_password}",
            )
        else:
            print(f"    Note: Set password with: arch-chroot /mnt passwd {username}")

        if self._config.docker.enabled:
            result = self._runner.run_as_chroot("pacman -Q docker", raise_on_nonzero_exit=False)
            if result.success:
                groups_result = self._runner.run_as_chroot(
                    f"groups {username}", raise_on_nonzero_exit=False
                )
                if "docker" not in groups_result.stdout:
                    print(f"    Adding {username} to docker group...")
                    self._runner.run_as_chroot(f"usermod -aG docker {username}")

    def _configure_docker(self) -> None:
        print(">>>>> Configuring Docker...")

        docker_config = self._config.docker

        result = self._runner.run_as_chroot("pacman -Q docker", raise_on_nonzero_exit=False)
        if not result.success:
            print("    Docker not installed, skipping configuration.")
            return

        print(f"    Storage driver: {docker_config.storage_driver}")
        print(f"    Data root: {docker_config.data_root}")

        self._runner.run("mkdir -p /mnt/etc/docker")

        daemon_json = f"""{{"storage-driver": "{docker_config.storage_driver}",
    "data-root": "{docker_config.data_root}"
}}"""
        self._runner.run(f"cat > /mnt/etc/docker/daemon.json << 'EOF'\n{daemon_json}\nEOF")

        self._runner.run_as_chroot("systemctl enable docker.service", raise_on_nonzero_exit=False)

        self._create_docker_access_group(docker_config.access_group)

        print(">>>>> Docker configuration complete.")

    def _create_docker_access_group(self, access_group: str) -> None:
        print(f"    Creating docker access group: {access_group}")

        result = self._runner.run_as_chroot(
            f"getent group {access_group}", raise_on_nonzero_exit=False
        )
        if not result.success:
            self._runner.run_as_chroot(f"groupadd {access_group}")

        username = self._system_config.user.name
        groups_result = self._runner.run_as_chroot(
            f"groups {username}", raise_on_nonzero_exit=False
        )
        if access_group not in groups_result.stdout:
            print(f"    Adding {username} to {access_group} group...")
            self._runner.run_as_chroot(f"usermod -aG {access_group} {username}")

        sudoers_content = f"# allow {access_group} group to run docker without password\n%{access_group} ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose"
        sudoers_file = f"/mnt/etc/sudoers.d/{access_group}"
        self._runner.run(f"cat > {sudoers_file} << 'EOF'\n{sudoers_content}\nEOF")
        self._runner.run(f"chmod 440 {sudoers_file}")
