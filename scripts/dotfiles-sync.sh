#!/usr/bin/env bash
#
# Dotfiles Sync - Push/Pull configuration files to git repo
#
# this script synchronizes configuration files between the local system and
# a git repository. It tracks specific config files across different
# locations and maintains version history.
#
# features:
#   - Maps config files from various locations (home, etc, XDG dirs)
#   - Incremental versioning with automatic commit messages
#   - KeePassXC SSH agent integration
#   - Desktop notifications on success/failure
#   - Dry-run mode for testing
#   - Pull mode to restore configs from repo
#
# usage:
#   ./dotfiles-sync.sh push [--dry-run]   # Push local changes to repo
#   ./dotfiles-sync.sh pull [--dry-run]   # Pull repo changes to local
#   ./dotfiles-sync.sh status             # Show sync status
#   ./dotfiles-sync.sh diff               # Show pending changes
#   ./dotfiles-sync.sh init <repo-url>    # Initialize with a repo
#
# requirements:
#   - git with SSH configured
#   - KeePassXC with SSH agent enabled
#   - yq (for config parsing, optional)
#   - notify-send (for notifications, optional)
# =============================================================================

set -Eeuo pipefail

# =============================================================================
# configuration
# =============================================================================

# default dotfiles repo location
DOTFILES_REPO="${DOTFILES_REPO:-$HOME/.dotfiles-repo}"
DOTFILES_CONFIG="${DOTFILES_CONFIG:-$HOME/.config/dotfiles-sync/config.yaml}"
VERSION_FILE="$DOTFILES_REPO/.version"
LOCK_FILE="/tmp/dotfiles-sync.lock"

# notification settings
NOTIFY_ENABLED="${DOTFILES_NOTIFY:-true}"
NOTIFY_ICON_SUCCESS="dialog-ok"
NOTIFY_ICON_ERROR="dialog-error"
NOTIFY_ICON_INFO="dialog-information"
APP_NAME="Dotfiles Sync"

# sensitive files - push is allowed, pull requires --force
# these are system configs that may have local modifications worth preserving
declare -a SENSITIVE_FILES=(
    "/etc/pacman.conf"
    "/etc/makepkg.conf"
    "/etc/mkinitcpio.conf"
    "/etc/fstab"
)

# default file mappings (source -> destination in repo)
# format: "local_path:repo_path"
# these are defaults; can be overridden in config file
declare -a DEFAULT_FILE_MAPPINGS=(
    # shell
    "$HOME/.zshrc:zsh/.zshrc"
    "$HOME/.zshenv:zsh/.zshenv"
    "$HOME/.zprofile:zsh/.zprofile"
    "$HOME/.bashrc:bash/.bashrc"
    "$HOME/.bash_profile:bash/.bash_profile"

    # git
    "$HOME/.gitconfig:git/.gitconfig"
    "$HOME/.gitignore_global:git/.gitignore_global"

    # terminal emulators
    "$HOME/.config/kitty:kitty"
    "$HOME/.config/alacritty:alacritty"
    "$HOME/.config/wezterm:wezterm"

    # editors
    "$HOME/.config/nvim:nvim"
    "$HOME/.config/Code/User/settings.json:vscode/settings.json"
    "$HOME/.config/Code/User/keybindings.json:vscode/keybindings.json"
    "$HOME/.config/Code/User/snippets:vscode/snippets"
    "$HOME/.vimrc:vim/.vimrc"

    # hyprland desktop
    "$HOME/.config/hypr:hypr"
    "$HOME/.config/waybar:waybar"
    "$HOME/.config/rofi:rofi"
    "$HOME/.config/dunst:dunst"
    "$HOME/.config/hyprpaper:hyprpaper"
    "$HOME/.config/hyprlock:hyprlock"

    # kde plasma desktop (partial - see notes)
    "$HOME/.config/kdeglobals:kde/kdeglobals"
    "$HOME/.config/kwinrc:kde/kwinrc"
    "$HOME/.config/plasmarc:kde/plasmarc"
    "$HOME/.config/plasma-org.kde.plasma.desktop-appletsrc:kde/plasma-appletsrc"
    "$HOME/.config/kglobalshortcutsrc:kde/kglobalshortcutsrc"
    "$HOME/.config/konsolerc:kde/konsolerc"
    "$HOME/.config/dolphinrc:kde/dolphinrc"
    "$HOME/.local/share/konsole:kde/konsole-profiles"

    # gnome desktop (dconf - see notes)
    # gnome settings are in dconf database, use dconf dump/load

    # other window managers
    "$HOME/.config/i3:i3"
    "$HOME/.config/sway:sway"
    "$HOME/.config/picom:picom"

    # development
    "$HOME/.config/starship.toml:starship/starship.toml"
    "$HOME/.tmux.conf:tmux/.tmux.conf"
    "$HOME/.config/tmux:tmux"

    # system (may need sudo)
    "/etc/pacman.conf:system/pacman.conf"
    "/etc/makepkg.conf:system/makepkg.conf"
)

# colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Utility functions

# check if a file is in the sensitive list
is_sensitive_file() {
    local file="$1"
    for sensitive in "${SENSITIVE_FILES[@]}"; do
        if [[ "$file" == "$sensitive" ]]; then
            return 0
        fi
    done
    return 1
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

die() {
    log_error "$*"
    notify_error "$*"
    exit 1
}

# Notification functions

# send desktop notification
notify() {
    local urgency="$1"  # low, normal, critical
    local title="$2"
    local message="$3"
    local icon="${4:-$NOTIFY_ICON_INFO}"

    if [ "$NOTIFY_ENABLED" != "true" ]; then
        return 0
    fi

    # try notify-send (works on most Linux DEs)
    if command -v notify-send &>/dev/null; then
        notify-send -u "$urgency" -i "$icon" -a "$APP_NAME" "$title" "$message" 2>/dev/null || true
        return 0
    fi

    # fallback: Try kdialog for KDE
    if command -v kdialog &>/dev/null; then
        case "$urgency" in
            critical) kdialog --error "$message" --title "$title" 2>/dev/null &;;
            *) kdialog --passivepopup "$message" 5 --title "$title" 2>/dev/null &;;
        esac
        return 0
    fi

    # fallback: Try zenity for GNOME
    if command -v zenity &>/dev/null; then
        zenity --notification --text="$title: $message" 2>/dev/null &
        return 0
    fi

    # no notification tool available - silent fail
    return 0
}

notify_info() {
    notify "normal" "$APP_NAME" "$*" "$NOTIFY_ICON_INFO"
}

notify_start() {
    local operation="${1:-sync}"
    notify "normal" "$APP_NAME" "Starting $operation..." "$NOTIFY_ICON_INFO"
}

notify_success() {
    local operation="${1:-sync}"
    local version="${2:-}"
    local message="$operation completed successfully"
    [ -n "$version" ] && message="$operation completed - v$version"
    notify "normal" "$APP_NAME - Success" "$message" "$NOTIFY_ICON_SUCCESS"
}

notify_error() {
    notify "critical" "$APP_NAME - Error" "$*" "$NOTIFY_ICON_ERROR"
}

# show GUI error dialog (blocking)
show_error_dialog() {
    local message="$1"

    # try zenity first (GTK)
    if command -v zenity &>/dev/null; then
        zenity --error --title="$APP_NAME - Error" --text="$message" --width=400 2>/dev/null || true
        return 0
    fi

    # try kdialog (KDE)
    if command -v kdialog &>/dev/null; then
        kdialog --error "$message" --title "$APP_NAME - Error" 2>/dev/null || true
        return 0
    fi

    # no GUI available
    return 0
}

# show GUI confirmation dialog
show_confirm_dialog() {
    local message="$1"

    # try zenity first (GTK)
    if command -v zenity &>/dev/null; then
        zenity --question --title="$APP_NAME" --text="$message" --width=400 2>/dev/null
        return $?
    fi

    # try kdialog (KDE)
    if command -v kdialog &>/dev/null; then
        kdialog --yesno "$message" --title "$APP_NAME" 2>/dev/null
        return $?
    fi

    # no GUI available - assume yes
    return 0
}

# acquire lock to prevent concurrent runs
acquire_lock() {
    if [ -f "$LOCK_FILE" ]; then
        local process_id
        process_id=$(cat "$LOCK_FILE")
        if kill -0 "$process_id" 2>/dev/null; then
            die "Another instance is running (PID: $process_id). Exiting."
        else
            log_warn "Stale lock file found. Removing..."
            rm -f "$LOCK_FILE"
        fi
    fi
    echo $$ > "$LOCK_FILE"
    trap 'rm -f "$LOCK_FILE"' EXIT
}

# check if KeePassXC is running and SSH agent is available
check_keepassxc() {
    log_info "Checking KeePassXC SSH agent..."

    # check if KeePassXC is running
    if ! pgrep -x "keepassxc" &>/dev/null; then
        log_error "KeePassXC is not running."
        notify_error "KeePassXC is not running. Please start KeePassXC and unlock your database."
        show_error_dialog "KeePassXC is not running.\n\nPlease start KeePassXC and unlock your database to enable SSH agent."
        return 1
    fi

    log_info "KeePassXC is running."

    # check if SSH_AUTH_SOCK is set (KeePassXC sets this when SSH agent is enabled)
    if [ -z "${SSH_AUTH_SOCK:-}" ]; then
        log_error "SSH_AUTH_SOCK is not set."
        log_error "Please enable SSH Agent in KeePassXC settings:"
        log_error "  Tools → Settings → SSH Agent → Enable SSH Agent integration"
        notify_error "SSH Agent not configured. Enable it in KeePassXC settings."
        show_error_dialog "SSH Agent is not configured.\n\nPlease enable SSH Agent in KeePassXC:\nTools → Settings → SSH Agent → Enable SSH Agent integration"
        return 1
    fi

    # check if any keys are available
    if ! ssh-add -l &>/dev/null; then
        log_error "No SSH keys available from KeePassXC."
        log_error "Please ensure:"
        log_error "  1. Your database is unlocked"
        log_error "  2. SSH keys are added to entries"
        log_error "  3. 'Add key to agent when database is opened' is enabled"
        notify_error "No SSH keys loaded. Unlock KeePassXC database."
        show_error_dialog "No SSH keys available.\n\nPlease ensure:\n1. Your KeePassXC database is unlocked\n2. SSH keys are attached to entries\n3. 'Add key to agent when database is opened' is enabled"
        return 1
    fi

    log_success "SSH keys available from KeePassXC"
    return 0
}

# test SSH connection to git server (extracted from URL)
test_git_connection() {
    local remote_url="$1"

    # extract host from git URL
    local git_host=""
    if [[ "$remote_url" =~ ^git@([^:]+): ]]; then
        # SSH format: git@host:user/repo.git
        git_host="${BASH_REMATCH[1]}"
    elif [[ "$remote_url" =~ ^ssh://git@([^/]+) ]]; then
        # SSH URL format: ssh://git@host/user/repo.git
        git_host="${BASH_REMATCH[1]}"
    elif [[ "$remote_url" =~ ^https?://([^/]+) ]]; then
        # HTTPS format: https://host/user/repo.git
        git_host="${BASH_REMATCH[1]}"
        log_info "HTTPS remote detected - skipping SSH test"
        return 0
    fi

    if [[ -z "$git_host" ]]; then
        log_warn "Could not extract host from remote URL: $remote_url"
        return 0
    fi

    log_info "Testing SSH connection to $git_host..."
    local ssh_output
    ssh_output=$(ssh -T "git@$git_host" 2>&1 || true)

    # different git servers have different success messages
    # GitHub: "Hi username! You've successfully authenticated"
    # GitLab: "Welcome to GitLab, @username!"
    # Gitea/Forgejo: "Hi there, username!"
    # Bitbucket: "logged in as username"
    if echo "$ssh_output" | grep -qiE "successfully authenticated|welcome|hi |logged in|hello"; then
        log_success "SSH connection to $git_host OK"
        return 0
    else
        log_warn "SSH test to $git_host returned: $ssh_output"
        log_warn "This may be normal for some git servers. Continuing..."
        return 0
    fi
}

# legacy function name for compatibility
check_ssh() {
    check_keepassxc
}

# check file permissions
check_permissions() {
    local file="$1"
    local mode="$2"  # "read" or "write"

    if [ ! -e "$file" ]; then
        return 0  # File doesn't exist yet
    fi

    if [ "$mode" = "read" ]; then
        if [ ! -r "$file" ]; then
            log_error "Cannot read: $file (permission denied)"
            return 1
        fi
    elif [ "$mode" = "write" ]; then
        local directory
        directory=$(dirname "$file")
        if [ ! -w "$directory" ]; then
            log_error "Cannot write to directory: $directory (permission denied)"
            log_error "You may need to run with sudo for system files"
            return 1
        fi
    fi

    return 0
}

# get current version number
get_version() {
    if [ -f "$VERSION_FILE" ]; then
        cat "$VERSION_FILE"
    else
        echo "0.0.0"
    fi
}

# increment version number
increment_version() {
    local version
    version=$(get_version)

    local major minor patch
    IFS='.' read -r major minor patch <<< "$version"

    # increment patch version
    patch=$((patch + 1))

    # roll over to minor if patch > 99
    if [ "$patch" -gt 99 ]; then
        patch=0
        minor=$((minor + 1))
    fi

    # roll over to major if minor > 99
    if [ "$minor" -gt 99 ]; then
        minor=0
        major=$((major + 1))
    fi

    echo "$major.$minor.$patch"
}

# load file mappings from config or use defaults
load_file_mappings() {
    local -n mappings_reference=$1

    # start with defaults
    mappings_reference=("${DEFAULT_FILE_MAPPINGS[@]}")

    # override/extend with config file if it exists
    if [ -f "$DOTFILES_CONFIG" ]; then
        log_info "Loading config from $DOTFILES_CONFIG"

        # read custom mappings from YAML config
        if command -v yq &>/dev/null; then
            while IFS= read -r line; do
                [ -n "$line" ] && mappings_reference+=("$line")
            done < <(yq -r '.mappings[]? // empty' "$DOTFILES_CONFIG" 2>/dev/null || true)

            # read repo path override
            local repository_path
            repository_path=$(yq -r '.repo_path // empty' "$DOTFILES_CONFIG" 2>/dev/null || true)
            [ -n "$repository_path" ] && DOTFILES_REPO="$repository_path"
        fi
    fi
}

# =============================================================================
# core Functions
# =============================================================================

# initialize dotfiles repository
init_repository() {
    local repository_url="$1"

    log_info "Initializing dotfiles repository..."

    if [ -d "$DOTFILES_REPO" ]; then
        log_warn "Repository already exists at $DOTFILES_REPO"
        read -rp "Remove and re-clone? (y/N): " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            rm -rf "$DOTFILES_REPO"
        else
            die "Aborted."
        fi
    fi

    # check SSH first
    check_ssh || die "SSH setup required before cloning"

    # test connection to git server
    test_git_connection "$repository_url"

    log_info "Cloning $repository_url to $DOTFILES_REPO..."
    if ! git clone "$repository_url" "$DOTFILES_REPO"; then
        die "Failed to clone repository. Check the URL and your SSH setup."
    fi

    # initialize version file if not present
    if [ ! -f "$VERSION_FILE" ]; then
        echo "1.0.0" > "$VERSION_FILE"
    fi

    # create config directory
    mkdir -p "$(dirname "$DOTFILES_CONFIG")"

    log_success "Repository initialized at $DOTFILES_REPO"
}

# show current status
show_status() {
    log_info "Dotfiles Sync Status"
    echo "================================"
    echo "Repository: $DOTFILES_REPO"
    echo "Version: $(get_version)"
    echo ""

    if [ ! -d "$DOTFILES_REPO" ]; then
        log_error "Repository not initialized. Run: $0 init <repo-url>"
        return 1
    fi

    # show git status
    echo "Git Status:"
    (cd "$DOTFILES_REPO" && git status --short)

    echo ""
    echo "Tracked Files:"

    local -a mappings
    load_file_mappings mappings

    for mapping in "${mappings[@]}"; do
        local source_path="${mapping%%:*}"
        local destination_path="${mapping#*:}"
        local expanded_source_path="${source_path/#\~/$HOME}"

        if [ -e "$expanded_source_path" ]; then
            if [ -e "$DOTFILES_REPO/$destination_path" ]; then
                echo -e "  ${GREEN}✓${NC} $source_path -> $destination_path"
            else
                echo -e "  ${YELLOW}+${NC} $source_path -> $destination_path (not in repo)"
            fi
        else
            echo -e "  ${RED}✗${NC} $source_path -> $destination_path (missing locally)"
        fi
    done
}

# show diff between local and repo
show_diff() {
    if [ ! -d "$DOTFILES_REPO" ]; then
        die "Repository not initialized. Run: $0 init <repo-url>"
    fi

    log_info "Showing differences..."

    local -a mappings
    load_file_mappings mappings
    local has_diff=false

    for mapping in "${mappings[@]}"; do
        local source_path="${mapping%%:*}"
        local destination_path="${mapping#*:}"
        local expanded_source_path="${source_path/#\~/$HOME}"
        local repository_file="$DOTFILES_REPO/$destination_path"

        if [ -e "$expanded_source_path" ] && [ -e "$repository_file" ]; then
            if [ -d "$expanded_source_path" ]; then
                # directory diff
                if ! diff -rq "$expanded_source_path" "$repository_file" &>/dev/null; then
                    echo -e "\n${YELLOW}=== $source_path (directory) ===${NC}"
                    diff -r "$expanded_source_path" "$repository_file" 2>/dev/null || true
                    has_diff=true
                fi
            else
                # file diff
                if ! diff -q "$expanded_source_path" "$repository_file" &>/dev/null; then
                    echo -e "\n${YELLOW}=== $source_path ===${NC}"
                    diff --color=auto "$expanded_source_path" "$repository_file" 2>/dev/null || true
                    has_diff=true
                fi
            fi
        elif [ -e "$expanded_source_path" ] && [ ! -e "$repository_file" ]; then
            echo -e "\n${GREEN}+++ $source_path (new file)${NC}"
            has_diff=true
        elif [ ! -e "$expanded_source_path" ] && [ -e "$repository_file" ]; then
            echo -e "\n${RED}--- $source_path (deleted locally)${NC}"
            has_diff=true
        fi
    done

    if [ "$has_diff" = false ]; then
        log_success "No differences found. Everything is in sync."
    fi
}

# push local changes to repository
push_changes() {
    local dry_run="${1:-false}"

    if [ ! -d "$DOTFILES_REPO" ]; then
        die "Repository not initialized. Run: $0 init <repo-url>"
    fi

    acquire_lock

    # check SSH setup (skip in CI/test environments)
    if [ "${DOTFILES_SKIP_SSH_CHECK:-false}" != "true" ]; then
        check_ssh || die "SSH setup required for push"
    fi

    # notify start
    [ "$dry_run" = false ] && notify_start "push"

    log_info "Pushing local changes to repository..."
    [ "$dry_run" = true ] && log_warn "DRY RUN - no changes will be made"

    local -a mappings
    load_file_mappings mappings
    local changes_made=false

    # test connection to git server before pull/push
    local remote_url
    remote_url=$(cd "$DOTFILES_REPO" && git remote get-url origin 2>/dev/null || echo "")
    if [[ -n "$remote_url" ]]; then
        test_git_connection "$remote_url"
    fi

    # pull latest changes first to avoid conflicts
    if [ "$dry_run" = false ]; then
        log_info "Pulling latest changes..."
        (cd "$DOTFILES_REPO" && git pull --rebase origin main 2>/dev/null) || \
        (cd "$DOTFILES_REPO" && git pull --rebase origin master 2>/dev/null) || \
        log_warn "Could not pull (may be first push)"
    fi

    for mapping in "${mappings[@]}"; do
        local source_path="${mapping%%:*}"
        local destination_path="${mapping#*:}"
        local expanded_source_path="${source_path/#\~/$HOME}"
        local repository_file="$DOTFILES_REPO/$destination_path"

        if [ ! -e "$expanded_source_path" ]; then
            continue
        fi

        # check read permissions
        if ! check_permissions "$expanded_source_path" "read"; then
            log_warn "Skipping $source_path (permission denied)"
            continue
        fi

        # create destination directory
        local repository_directory
        repository_directory=$(dirname "$repository_file")

        if [ "$dry_run" = false ]; then
            mkdir -p "$repository_directory"
        fi

        # check if file/dir has changed
        local needs_update=false
        if [ -d "$expanded_source_path" ]; then
            # directory comparison
            if [ ! -d "$repository_file" ] || ! diff -rq "$expanded_source_path" "$repository_file" &>/dev/null; then
                needs_update=true
            fi
        else
            # file comparison
            if [ ! -f "$repository_file" ] || ! diff -q "$expanded_source_path" "$repository_file" &>/dev/null; then
                needs_update=true
            fi
        fi

        if [ "$needs_update" = true ]; then
            if [ "$dry_run" = true ]; then
                log_info "[DRY RUN] Would copy: $source_path -> $destination_path"
            else
                if [ -d "$expanded_source_path" ]; then
                    rm -rf "$repository_file"
                    cp -r "$expanded_source_path" "$repository_file"
                else
                    cp "$expanded_source_path" "$repository_file"
                fi
                log_success "Updated: $source_path"
            fi
            changes_made=true
        fi
    done

    if [ "$changes_made" = false ]; then
        log_success "No changes to push. Everything is in sync."
        return 0
    fi

    if [ "$dry_run" = true ]; then
        log_info "[DRY RUN] Would commit and push changes"
        return 0
    fi

    # commit and push
    local new_version
    new_version=$(increment_version)
    echo "$new_version" > "$VERSION_FILE"

    local commit_message="dotfiles v$new_version - $(date '+%Y-%m-%d %H:%M')"

    (
        cd "$DOTFILES_REPO"
        git add -A
        git commit -m "$commit_message" || {
            log_warn "Nothing to commit"
            return 0
        }

        log_info "Pushing to remote..."
        if ! git push origin HEAD; then
            log_error "Push failed. You may need to resolve conflicts manually."
            log_error "Repository location: $DOTFILES_REPO"
            notify_error "Push failed - conflicts may need manual resolution"
            return 1
        fi
    )

    log_success "Pushed version $new_version"
    notify_success "push" "$new_version"
}

# pull changes from repository to local
# sensitive files are skipped unless --force is passed
pull_changes() {
    local dry_run="${1:-false}"
    local force="${2:-false}"

    if [ ! -d "$DOTFILES_REPO" ]; then
        die "Repository not initialized. Run: $0 init <repo-url>"
    fi

    acquire_lock

    # check SSH setup (skip in CI/test environments)
    if [ "${DOTFILES_SKIP_SSH_CHECK:-false}" != "true" ]; then
        check_ssh || die "SSH setup required for pull"
    fi

    # notify start
    [ "$dry_run" = false ] && notify_start "pull"

    log_info "Pulling changes from repository..."
    [ "$dry_run" = true ] && log_warn "DRY RUN - no changes will be made"
    [ "$force" = true ] && log_warn "FORCE MODE - sensitive files will be overwritten"

    # pull latest
    if [ "$dry_run" = false ]; then
        (cd "$DOTFILES_REPO" && git pull origin main 2>/dev/null) || \
        (cd "$DOTFILES_REPO" && git pull origin master 2>/dev/null) || {
            notify_error "Failed to pull from remote repository"
            die "Failed to pull from remote"
        }
    fi

    local -a mappings
    load_file_mappings mappings
    local changes_made=false
    local skipped_sensitive=()

    for mapping in "${mappings[@]}"; do
        local source_path="${mapping%%:*}"
        local destination_path="${mapping#*:}"
        local expanded_source_path="${source_path/#\~/$HOME}"
        local repository_file="$DOTFILES_REPO/$destination_path"

        if [ ! -e "$repository_file" ]; then
            continue
        fi

        # check if sensitive file and skip unless --force
        if is_sensitive_file "$expanded_source_path" && [ "$force" != true ]; then
            skipped_sensitive+=("$expanded_source_path")
            log_warn "Skipping sensitive file: $source_path (use --force to overwrite)"
            continue
        fi

        # check write permissions
        if ! check_permissions "$expanded_source_path" "write"; then
            log_warn "Skipping $source_path (permission denied - may need sudo)"
            continue
        fi

        # check if update needed
        local needs_update=false
        if [ -d "$repository_file" ]; then
            if [ ! -d "$expanded_source_path" ] || ! diff -rq "$repository_file" "$expanded_source_path" &>/dev/null; then
                needs_update=true
            fi
        else
            if [ ! -f "$expanded_source_path" ] || ! diff -q "$repository_file" "$expanded_source_path" &>/dev/null; then
                needs_update=true
            fi
        fi

        if [ "$needs_update" = true ]; then
            if [ "$dry_run" = true ]; then
                log_info "[DRY RUN] Would restore: $destination_path -> $source_path"
            else
                # create parent directory
                mkdir -p "$(dirname "$expanded_source_path")"

                # backup existing file
                if [ -e "$expanded_source_path" ]; then
                    local backup="$expanded_source_path.backup.$(date +%s)"
                    cp -r "$expanded_source_path" "$backup"
                    log_info "Backed up: $source_path -> $backup"
                fi

                # copy from repo
                if [ -d "$repository_file" ]; then
                    rm -rf "$expanded_source_path"
                    cp -r "$repository_file" "$expanded_source_path"
                else
                    cp "$repository_file" "$expanded_source_path"
                fi
                log_success "Restored: $source_path"
            fi
            changes_made=true
        fi
    done

    # report skipped sensitive files
    if [ ${#skipped_sensitive[@]} -gt 0 ]; then
        log_warn "Skipped ${#skipped_sensitive[@]} sensitive file(s):"
        for skipped_file in "${skipped_sensitive[@]}"; do
            log_warn "  - $skipped_file"
        done
        log_info "Use 'pull --force' to overwrite these files"
    fi

    if [ "$changes_made" = false ] && [ ${#skipped_sensitive[@]} -eq 0 ]; then
        log_success "No changes to pull. Everything is in sync."
        return 0
    fi

    if [ "$dry_run" = false ]; then
        local version
        version=$(get_version)
        log_success "Pull complete. Version: $version"
        notify_success "pull" "$version"
    fi
}

# =============================================================================
# main Entry Point
# =============================================================================

show_help() {
    cat <<EOF
Dotfiles Sync - Synchronize configuration files with GitHub

Usage: $0 <command> [options]

Commands:
    init <repo-url>             Initialize with a GitHub repository
    push [--dry-run]            Push local changes to repository
    pull [--dry-run] [--force]  Pull repository changes to local
    status                      Show sync status
    diff                        Show pending changes

Options:
    --dry-run           Show what would be done without making changes
    --force             Overwrite sensitive files (pacman.conf, etc.) during pull

Sensitive Files:
    Some system configs (pacman.conf, makepkg.conf, etc.) are protected during pull.
    These files can always be pushed but require --force to pull and overwrite local versions.

Environment Variables:
    DOTFILES_REPO       Repository location (default: ~/.dotfiles-repo)
    DOTFILES_CONFIG     Config file path (default: ~/.config/dotfiles-sync/config.yaml)

Examples:
    $0 init git@github.com:username/dotfiles.git
    $0 push
    $0 push --dry-run
    $0 pull
    $0 pull --force           # overwrite sensitive files too
    $0 status
    $0 diff

EOF
}

main() {
    local command="${1:-}"
    shift || true

    case "$command" in
        init)
            [ -z "${1:-}" ] && die "Usage: $0 init <repo-url>"
            init_repository "$1"
            ;;
        push)
            local dry_run=false
            [[ "${1:-}" == "--dry-run" ]] && dry_run=true
            push_changes "$dry_run"
            ;;
        pull)
            local dry_run=false
            local force=false
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --dry-run) dry_run=true ;;
                    --force) force=true ;;
                esac
                shift
            done
            pull_changes "$dry_run" "$force"
            ;;
        status)
            show_status
            ;;
        diff)
            show_diff
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            show_help
            exit 1
            ;;
    esac
}

main "$@"
