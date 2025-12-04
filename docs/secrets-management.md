# Secrets Management with KeePassXC

A comprehensive guide to managing SSH keys, GPG keys, and other secrets using KeePassXC, with cross-device synchronization via Syncthing.

## Overview

Instead of typing passwords repeatedly, KeePassXC can manage all your secrets with a single master password. This guide covers:

- SSH key management via ssh-agent integration
- Cross-device sync with Syncthing and KeePassDX (Android)
- Automatic database locking when you lock your screen

## Installation

KeePassXC is included in the `base` package profile of this installer, but can also be installed manually:

```bash
sudo pacman -S keepassxc
```

## SSH Key Integration

### Set Up ssh-agent

Add to your shell profile (`~/.bash_profile` for bash, `~/.profile` for sh/dash, or `~/.zprofile` for zsh):

```bash
# start ssh-agent if not running
if [ -z "$SSH_AUTH_SOCK" ]; then
    eval $(ssh-agent) > /dev/null
fi
```

Log out and back in for changes to take effect.

### Configure KeePassXC

1. Open KeePassXC Settings (Tools → Settings)
2. Go to **SSH Agent** section
3. Enable **"Enable SSH Agent integration"**
4. You should see a green message confirming the connection

### Add SSH Key to KeePassXC

1. Create a new entry for your SSH key
2. Set the password field to your SSH key passphrase
3. Go to the **SSH Agent** tab in the entry
4. Check **"Add key to agent when database is opened/unlocked"**
5. Check **"Remove key from agent when database is closed/locked"**
6. Under **Private key**, select **"External file"**
7. Browse to your private key (e.g., `~/.ssh/id_rsa` or `~/.ssh/id_ed25519`)

Now when you unlock your KeePassXC database, SSH will automatically have access to your keys.

### Test It

```bash
# should work without password prompt if database is unlocked
ssh user@server
```

### Enable Secret Service in KeePassXC

1. Open KeePassXC Settings
2. Go to **Secret Service Integration**
3. Enable **"Enable KeePassXC Freedesktop.org Secret Service integration"**

## Cross-Device Sync with Syncthing

Sync your KeePassXC database to your Android phone using KeePassDX.

### Install Syncthing

```bash
sudo pacman -S syncthing
systemctl --user enable --now syncthing
```

Access the web UI at `http://localhost:8384`

### Set Up Android

1. Install Syncthing on Android
2. Install KeePassDX on Android

### Configure Sync

**On your PC (Syncthing web UI):**

1. Go to Actions → Show ID
2. Note your device ID

**On Android (Syncthing app):**

1. Add your PC as a device using its ID
2. Accept the connection request on PC

**Create a shared folder:**

1. On PC: Actions → Add Folder
2. Folder Path: `/home/yourusername/.keepass` (create this directory)
3. Share with your Android device
4. Accept the folder on Android

**Move your database:**

```bash
mkdir -p ~/.keepass
mv ~/Documents/passwords.kdbx ~/.keepass/
```

Update KeePassXC to open from the new location.

### Security Considerations

- The database file is encrypted; syncing it is safe
- Use a strong master password
- Syncthing uses TLS for transit encryption

## Auto-Lock When Screen Locks

### Why This Matters

If your computer is stolen while KeePassXC is unlocked, all secrets are exposed. Auto-locking ensures secrets are cleared from memory when you're away.

### Configure KeePassXC

1. Settings → Security
2. Enable **"Lock databases when session is locked or lid is closed"**

### KDE/GNOME

Usually works automatically with the KeePassXC setting enabled.

### Manual Trigger for Custom Lock Commands

If auto-lock doesn't work with your desktop/WM, trigger it manually before locking:

```bash
dbus-send --print-reply --dest=org.keepassxc.KeePassXC.MainWindow /keepassxc org.keepassxc.KeePassXC.MainWindow.lockAllDatabases
```

## Browser Integration

KeePassXC can integrate with your browser, and autofill.

### Configure KeePassXC browser Integration

1. Install preferred browser extension
1. Settings → Browser Integration
1. Enable for your browser(s)
1. Click the extension icon in your browser
1. Click "Connect" and name the connection

## Best Practices

### Master Password

- Use a long passphrase
- Consider a physical backup in a safe location

### Key File (Optional Extra Security)

1. Create a key file: Database → Database Security → Add Key File
2. Store the key file separately from the database

### Backup Strategy

```bash
# automated backup script
#!/bin/bash
cp ~/.keepass/passwords.kdbx "/backup/location/passwords-$(date +%Y%m%d%H).kdbx"
```

### What to Store

| Store in KeePassXC  | Don't Store                           |
| ------------------- | ------------------------------------- |
| SSH key passphrases | SSH private keys (keep in ~/.ssh)     |
| GPG key passphrases | GPG private keys (keep in ~/.gnupg)   |
| Website passwords   | 2FA recovery codes (store separately) |
| API tokens          | Full disk encryption key              |

## Troubleshooting

### SSH Agent Not Working

```bash
# check if agent is running
echo $SSH_AUTH_SOCK
ssh-add -l

# restart agent
killall ssh-agent
eval $(ssh-agent)
```

### Syncthing Not Syncing

Check the web UI (`localhost:8384`) for:

- Connection status to remote device
- Folder sync status
- Any conflict files

## References

- [KeePassXC Documentation](https://keepassxc.org/docs/)
- [Syncthing Documentation](https://docs.syncthing.net/)
- [Arch Wiki - KeePass](https://wiki.archlinux.org/title/KeePass)
