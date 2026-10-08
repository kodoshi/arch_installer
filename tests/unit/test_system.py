from dataclasses import replace

from arch_installer.config.models import LocaleConfig, UserConfig
from arch_installer.executors.system import (
    SystemExecutor,
    hosts_file,
    locale_conf,
    locales_to_generate,
)
from tests.unit.conftest import build_config


class TestSystemTemplates:
    def test_hosts_file_maps_the_hostname(self):
        content = hosts_file("workstation")
        assert "127.0.1.1   workstation.localdomain workstation" in content
        assert "localhost" in content

    def test_locale_conf_sets_lang_and_only_differing_categories(self):
        locale = LocaleConfig(monetary="fr_FR.UTF-8")
        content = locale_conf(locale)
        assert "LANG=en_US.UTF-8" in content
        assert "LC_MONETARY=fr_FR.UTF-8" in content
        # a category equal to LANG is not repeated
        assert "LC_NUMERIC" not in content

    def test_locales_to_generate_is_deduplicated(self):
        locale = LocaleConfig(monetary="fr_FR.UTF-8", paper="fr_FR.UTF-8")
        generated = locales_to_generate(locale)
        assert generated == sorted(set(generated))
        assert "en_US.UTF-8" in generated
        assert "fr_FR.UTF-8" in generated


class TestSystemExecutor:
    def test_writes_hostname_hosts_and_locale(self, fake_runner):
        SystemExecutor(build_config(), fake_runner).execute()

        assert fake_runner.written_content("/mnt/etc/hostname") == "testhost\n"
        assert "testhost" in fake_runner.written_content("/mnt/etc/hosts")
        assert "LANG=" in fake_runner.written_content("/mnt/etc/locale.conf")

    def test_creates_the_user_with_its_groups_when_absent(self, fake_runner):
        fake_runner.set_response("id alice", exit_code=1)
        config = build_config(
            system=replace(
                build_config().system, user=UserConfig(name="alice", groups=("wheel", "video"))
            )
        )
        SystemExecutor(config, fake_runner).execute()

        useradd = fake_runner.get_commands("useradd")
        assert useradd and "-G wheel,video" in useradd[0] and "alice" in useradd[0]

    def test_sets_the_user_password_over_stdin(self, fake_runner):
        fake_runner.set_response("id testuser", exit_code=1)
        SystemExecutor(build_config(), fake_runner).execute()

        chpasswd = [
            command for command in fake_runner.recorded_commands if "chpasswd" in command.command
        ]
        assert chpasswd and chpasswd[-1].input_data == "testuser:userpassword"

    def test_skips_user_creation_when_it_exists(self, fake_runner):
        fake_runner.set_response("id testuser", exit_code=0)
        SystemExecutor(build_config(), fake_runner).execute()
        fake_runner.assert_command_not_called("useradd")
