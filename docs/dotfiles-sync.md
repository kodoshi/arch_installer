# Dotfiles Sync

Synchronize configuration files to a git repository with versioning, desktop notifications, and KeePassXC SSH agent integration.

## Features

- Bidirectional sync (push/pull)
- Automatic semantic versioning
- KeePassXC SSH agent integration
- Desktop notifications
- Dry-run mode
- Configurable file mappings

## Installation

The `dotfiles-sync` command is installed to `/usr/local/bin/` during system setup, making it available system-wide.

**Script location**: `/usr/local/bin/dotfiles-sync` (symlinked from project `scripts/dotfiles-sync.sh`)

To install manually:

```bash
sudo ln -sf /path/to/arch_installer/scripts/dotfiles-sync.sh /usr/local/bin/dotfiles-sync
```

## Quick Start

```bash
# initialize with your repo (system-wide command after install)
dotfiles-sync init git@github.com:username/dotfiles.git

# push current dotfiles
dotfiles-sync push

# on new machine, pull dotfiles
dotfiles-sync pull
```

## Commands

| Command                         | Description                                    |
| ------------------------------- | ---------------------------------------------- |
| `dotfiles-sync init <repo-url>` | Initialize with GitHub repository              |
| `dotfiles-sync push`            | Push local changes                             |
| `dotfiles-sync push --dry-run`  | Preview what would be pushed                   |
| `dotfiles-sync pull`            | Pull repository changes (skip sensitive files) |
| `dotfiles-sync pull --force`    | Pull all files including sensitive ones        |
| `dotfiles-sync pull --dry-run`  | Preview what would be pulled                   |
| `dotfiles-sync status`          | Show sync status                               |
| `dotfiles-sync diff`            | Show differences                               |

## Sensitive Files

Some system configs are protected during pull to prevent accidental overwrite of local customizations:

- `/etc/pacman.conf`
- `/etc/makepkg.conf`
- `/etc/mkinitcpio.conf`
- `/etc/fstab`

These files:

- **Can always be pushed** to the repository
- **Require `--force`** to pull and overwrite local versions

```bash
# push pacman.conf changes to repo (always works)
dotfiles-sync push

# pull won't touch pacman.conf
dotfiles-sync pull

# explicitly overwrite local pacman.conf
dotfiles-sync pull --force
```

## Configuration

Create `~/.config/dotfiles-sync/config.yaml`:

```yaml
repo_path: /home/user/.dotfiles-repo

mappings:
  - '~/.zshrc:zsh/.zshrc'
  - '~/.config/kitty:kitty'
  - '~/.config/hypr:hypr'
  - '~/.gitconfig:git/.gitconfig'
```

## Default Tracked Files

- **Shell**: `.zshrc`, `.bashrc`, `.zprofile`, `.bash_profile`
- **Git**: `.gitconfig`, `.gitignore_global`
- **Terminals**: kitty, alacritty, wezterm
- **Editors**: nvim, VS Code settings, `.vimrc`
- **Hyprland**: hypr/, waybar/, rofi/, dunst/, hyprpaper/, hyprlock/
- **KDE Plasma**: kdeglobals, kwinrc, plasmarc, shortcuts, konsole
- **GNOME**: Uses dconf (see below)
- **Development**: starship, tmux
- **System**: `/etc/pacman.conf`, `/etc/makepkg.conf`

## Desktop Environment Configs

### Hyprland

All Hyprland configs are plain files in `~/.config/hypr/` and are fully tracked.

### KDE Plasma

KDE configs are partially tracked:

| File                                                | Contains                    |
| --------------------------------------------------- | --------------------------- |
| `~/.config/kdeglobals`                              | Global theme, colors, fonts |
| `~/.config/kwinrc`                                  | Window manager, effects     |
| `~/.config/plasmarc`                                | Plasma shell theme          |
| `~/.config/plasma-org.kde.plasma.desktop-appletsrc` | Panel layout, widgets       |
| `~/.config/kglobalshortcutsrc`                      | Keyboard shortcuts          |

**Note**: Some KDE settings (like dock icons, desktop icons positions) are stored in other locations and will not sync perfectly.

### GNOME

GNOME stores settings in a binary **dconf database**, not plain files. Use dconf directly:

```bash
# export all gnome settings
dconf dump / > gnome-settings.dconf

# import on new machine
dconf load / < gnome-settings.dconf

# export specific section (e.g., just extensions)
dconf dump /org/gnome/shell/extensions/ > gnome-extensions.dconf
```

Add the exported `.dconf` file to your dotfiles mappings manually.

## KeePassXC Integration

If you are using KeePassXC with SSH agent integration, your database needs to be unlocked before running `dotfiles-sync`.

## Automated Daily Sync

```bash
mkdir -p ~/.config/systemd/user
cp config/systemd/dotfiles-sync.service ~/.config/systemd/user/
cp config/systemd/dotfiles-sync.timer ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now dotfiles-sync.timer
```

## Environment Variables

| Variable          | Description          | Default                               |
| ----------------- | -------------------- | ------------------------------------- |
| `DOTFILES_REPO`   | Repository location  | `~/.dotfiles-repo`                    |
| `DOTFILES_CONFIG` | Config file path     | `~/.config/dotfiles-sync/config.yaml` |
| `DOTFILES_NOTIFY` | Enable notifications | `true`                                |

## Desktop Notifications

| Event          | Message                                       |
| -------------- | --------------------------------------------- |
| Push started   | "Starting push..."                            |
| Push succeeded | "Push completed - vX.Y.Z"                     |
| Push failed    | "Push failed - conflicts may need resolution" |

Disable: `DOTFILES_NOTIFY=false dotfiles-sync.sh push`

## Troubleshooting

**SSH connection fails:**

```bash
pgrep keepassxc        # Check KeePassXC running
ssh-add -l             # Check keys loaded
ssh -T git@github.com  # Test GitHub connection
```

**Conflicts during push:**

```bash
cd ~/.dotfiles-repo
git status
git pull --rebase
# Resolve conflicts, then:
dotfiles-sync push
```
