"""hostname, timezone, locales, console keymap and the user account."""

import logging

from arch_installer.config.models import LocaleConfig
from arch_installer.core.command import CommandRunner
from arch_installer.executors.base import TARGET_ROOT, StepExecutor, write_file

logger = logging.getLogger(__name__)


def hosts_file(hostname: str) -> str:
    return f"""127.0.0.1   localhost
::1         localhost
127.0.1.1   {hostname}.localdomain {hostname}
"""


def locale_conf(locale: LocaleConfig) -> str:
    lines = [f"LANG={locale.full_locale}"]
    category_overrides = {
        "LC_MONETARY": locale.monetary,
        "LC_TIME": locale.time_format,
        "LC_NUMERIC": locale.numeric,
        "LC_PAPER": locale.paper,
    }
    lines.extend(
        f"{variable}={value}"
        for variable, value in category_overrides.items()
        if value and value != locale.full_locale
    )
    return "\n".join(lines) + "\n"


def locales_to_generate(locale: LocaleConfig) -> list[str]:
    candidates = {
        locale.full_locale,
        locale.monetary,
        locale.time_format,
        locale.numeric,
        locale.paper,
    }
    return sorted(candidate for candidate in candidates if candidate)


def user_groups(runner: CommandRunner, username: str) -> set[str]:
    return set(
        runner.run_as_chroot(f"id -nG {username}", raise_on_nonzero_exit=False).stdout.split()
    )


class SystemStepExecutor(StepExecutor):
    def execute(self) -> None:
        system = self._config.system
        logger.info(
            "Configuring %s (%s, %s)...",
            system.hostname,
            system.timezone,
            system.locale.full_locale,
        )

        write_file(self._runner, f"{TARGET_ROOT}/etc/hostname", f"{system.hostname}\n")
        write_file(self._runner, f"{TARGET_ROOT}/etc/hosts", hosts_file(system.hostname))

        self._runner.run(f"rm -f {TARGET_ROOT}/etc/localtime")
        self._runner.run(
            f"ln -sf /usr/share/zoneinfo/{system.timezone} {TARGET_ROOT}/etc/localtime"
        )
        self._runner.run_as_chroot("hwclock --systohc", raise_on_nonzero_exit=False)

        for locale in locales_to_generate(system.locale):
            self._runner.run(
                f"sed -i 's/^#\\s*\\({locale}\\s\\)/\\1/' {TARGET_ROOT}/etc/locale.gen",
                raise_on_nonzero_exit=False,
            )
        self._runner.run_as_chroot("locale-gen", raise_on_nonzero_exit=False)
        write_file(self._runner, f"{TARGET_ROOT}/etc/locale.conf", locale_conf(system.locale))
        write_file(
            self._runner, f"{TARGET_ROOT}/etc/vconsole.conf", f"KEYMAP={system.locale.keymap}\n"
        )

        self._create_user()

    def _create_user(self) -> None:
        user = self._config.system.user
        if self._runner.run_as_chroot(f"id {user.name}", raise_on_nonzero_exit=False).success:
            logger.info("User %s already exists", user.name)
        else:
            groups = ",".join(user.groups)
            self._runner.run_as_chroot(f"useradd -m -G {groups} -s /bin/bash {user.name}")
            logger.info("User %s created (groups: %s)", user.name, groups)
            self._runner.run(
                f"sed -i 's/^#\\s*\\(%wheel ALL=(ALL:ALL) ALL\\)/\\1/' {TARGET_ROOT}/etc/sudoers",
                raise_on_nonzero_exit=False,
            )

        self._runner.run_as_chroot(
            "chpasswd", input_data=f"{user.name}:{self._config.credentials.user_password}"
        )
