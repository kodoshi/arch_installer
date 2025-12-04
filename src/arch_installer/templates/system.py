"""Templates for system configuration files."""


def hosts_file(hostname: str) -> str:
    """Generate /etc/hosts content for a given hostname."""
    return f"""127.0.0.1   localhost
::1         localhost
127.0.1.1   {hostname}.localdomain {hostname}
"""


def vconsole_conf(keymap: str) -> str:
    """Generate /etc/vconsole.conf content."""
    return f"KEYMAP={keymap}\n"


def locale_conf(locale: str) -> str:
    """Generate /etc/locale.conf content."""
    return f"LANG={locale}\n"
