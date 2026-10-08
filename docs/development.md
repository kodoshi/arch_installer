# Development

## Project Structure

```
arch_installer/
├── config/
│   └── config.yaml              # main configuration, main source of truth
├── src/arch_installer/
│   ├── cli.py                   # entry points and config assembly
│   ├── installer.py             # runs the install steps
│   ├── install_steps/           # the registry: every step, its settings, questions, executor
│   ├── config/                  # config model, value sources and their precedence
│   ├── setup/                   # the interactive setup, independent of any front-end
│   ├── tui/                     # the curses front-end
│   ├── executors/               # one executor per install step
│   └── core/                    # command runner, logging, secrets crypto
├── scripts/                     # utilities installed on the target system
├── tests/
│   ├── unit/                    # fast, isolated tests
│   └── qemu/                    # full VM tests
└── docs/
```

## Running the Installer

### Using Make (Recommended)

The Makefile provides the canonical entry point:

```bash
make install          # Full installation: deps + run
make deps             # Install dependencies only
make run              # Run installer (assumes deps installed)
make lint             # ruff check + format check
make format           # ruff format + safe fixes
```

### With Environment Variables

```bash
read -rsp 'Secrets key: ' ARCH_INSTALLER_SECRETS_KEY && export ARCH_INSTALLER_SECRETS_KEY
NON_INTERACTIVE=true make install
```

Never type a secret into a command line, see [Keeping secrets out of ps and shell history](configuration.md#keeping-secrets-out-of-ps-and-shell-history).

Every variable is listed in the [Configuration reference](configuration.md#environment-variables).
Their names live in one place in the code: the `EnvVariable` enum in `config/environment.py`.

### Direct Python Execution

For development:

```bash
poetry install
poetry run arch-installer
```

Or without poetry (requires Python 3.13+):

```bash
pip install -e .
python -m arch_installer.cli
```

## Code Flow

### Three Phases

![Installer Flow](diagrams/installer-flow.png)

#### Phase 1: Configuration Assembly

`cli.assemble_installer_config()` reads it top to bottom:

1. `Environment.setting_values()`: the settings provided by environment variables that are set. Which variable provides which setting is declared with the install steps (`install_steps/registry.py`), and the text is read as the type the model declares for that setting
2. `config_file_setting_values()`: `config.yaml` flattened into setting paths such as `storage.swap.size_mb`, with its encrypted passwords unlocked by `ARCH_INSTALLER_SECRETS_KEY` (`config/config_file.py`)
3. `inherit_setting_values()`: for each setting, the first source in `INHERITANCE_ORDER = (ENVIRONMENT, CONFIG_FILE)` that has a value gives the inherited value, remembered with its source (`config/value_precedence.py`)
4. interactive only: the setup session (`setup/session.py`) walks the install steps and asks each question through a front-end, showing the inherited value with its source; `apply_tui_choices()` lets the answers win
5. `build_installer_config()`: the values become the frozen `InstallerConfig`; every field must have a value, and all settings no source provided are reported together (`config/installer_config_builder.py`)

The result is validated (`validate_for_install`) and is immutable from then on. Nothing comes from code: the model has no defaults to fall back on.

#### Phase 2: Orchestration

`install_steps/registry.py` holds `INSTALL_STEPS`, a dict from `InstallStep` to `StepWiring`, in the order the steps run. Each `StepWiring` lists the step's config.yaml sections, its settings (`StepSetting`: config key, environment variable, question), the condition under which it runs and its executor class. The `Installer` runs the executor of every step whose condition holds; every executor receives the same finished `InstallerConfig`, so a choice is decided in exactly one place: the config.

#### Phase 3: Command Execution

Each executor uses a `CommandRunner` to execute shell commands. This abstraction exists for testability:

- `SystemCommandRunner`: runs real subprocess calls
- `FakeCommandRunner`: records commands for unit tests

### One Config Model

There is a single frozen dataclass tree, `InstallerConfig`, and it has no field defaults (the one exception, `UsbBootConfig`, keeps them only for a unit test and the builder still requires every field). Values are addressed by their dotted path in the model while they are being assembled, and become the model once, at the end, so every executor and the generated `final_config.yaml` see the values that were actually installed. Passwords live in `InstallerConfig.credentials` and are left out of `final_config.yaml` by `exportable_config()`.

## File Layout

```
src/arch_installer/
├── cli.py                      # entry points and the config assembly
├── installer.py                # Installer: runs the enabled install steps in order
├── expected_state.py           # the values verify-install checks the installed system against
├── errors.py                   # custom exceptions
├── config/
│   ├── models.py               # InstallerConfig and its sections (frozen, no defaults), enums
│   ├── value_precedence.py     # where each value comes from and which source wins
│   ├── environment.py          # EnvVariable names, reads the variables that are set
│   ├── config_file.py          # config.yaml -> setting values, encrypted passwords unlocked
│   ├── installer_config_builder.py  # setting values -> InstallerConfig, missing ones reported
│   └── secrets_file.py         # writes encrypted passwords into config.yaml, keeping comments
├── install_steps/
│   ├── registry.py             # InstallStep, INSTALL_STEPS: settings, questions, conditions, executors
│   ├── wiring.py               # StepWiring, StepSetting, conditions (always, when, when_equal)
│   └── questions.py            # what can be asked (choose one or several, text, secret, switch)
├── setup/
│   ├── session.py              # walks the steps and asks through a front-end
│   ├── frontend.py             # SetupFrontend: the port every front-end implements
│   └── machine.py              # detected disks, offered as choices
├── core/
│   ├── command.py              # CommandRunner interface, SystemCommandRunner
│   ├── log.py                  # stdlib logging setup (stdout progress, stderr problems)
│   └── secrets.py              # Argon2id + AES-256-GCM encryption of stored passwords
├── executors/
│   ├── base.py                 # StepExecutor base class, shared shell helpers
│   ├── storage.py              # disk wipe, partitions, LUKS, BTRFS, swap
│   ├── mirrors.py              # pacman mirrorlist
│   ├── packages.py             # pacstrap, fstab, display manager
│   ├── system.py               # hostname, locale, user
│   ├── docker.py               # Docker daemon and access group
│   ├── gpu.py                  # proprietary NVIDIA driver setup
│   ├── boot.py                 # mkinitcpio, UKI variants, secure boot, systemd-boot
│   ├── snapper.py              # snapshots, bootable snapshots, notifications
│   ├── migration.py            # migration from an existing install
│   ├── firewall.py             # UFW setup (configured offline, enabled on boot)
│   ├── usb_boot.py             # USB boot drive: layout, safeguards, spare clone
│   ├── recovery.py             # signed recovery UKI from the Arch ISO
│   └── usb_backup.py           # backup to a partition of the user's choice
└── tui/
    ├── curses_frontend.py      # CursesFrontend: renders each kind of question
    └── widgets.py              # curses widgets on one list engine, and the text field

scripts/                        # installed to /usr/local/bin on the target
├── manage_snapshot_entries.sh  # manage-snapshot-ukis
├── verify_install.sh           # verify-install (also `make verify`)
└── dotfiles-sync.sh            # dotfiles-sync

docs/
├── diagrams/
│   └── architecture.puml       # PlantUML class diagram
├── functional-map.md           # every entry point, module and test, mapped
├── development.md              # this file
└── ...                         # other documentation
```

## Architecture Documentation

### UML Class Diagram

A PlantUML class diagram is available at `docs/diagrams/architecture.puml`. It shows:

- The entry points, the config model and how it is resolved
- The install step registry, the setup session with its front-end port, and the executors
- The `CommandRunner` port that executors run every command through

To generate the diagram:

```bash
# requires plantuml installed
make diagrams
```

### Functional Map

`docs/functional-map.md` maps every entry point, module, config section and test to what it does. `docs/code-analysis.md` is a historical analysis of the code before the restructure and no longer matches it.

## Adding a New Step

1. Add its config section to `config/models.py` (a frozen dataclass, no defaults) and a field for it on `InstallerConfig`
2. Add the section with every key to `config/config.yaml` and the configs in `tests/data/` (the builder refuses a config that lacks any of them)
3. Create `executors/new_step.py` with an executor class (`NewStepStepExecutor`) implementing `execute()`
4. Add a member to `InstallStep` and its `StepWiring` to `INSTALL_STEPS`, at the position it should run: its config sections, its settings with their environment variable and question, the condition and the executor
5. Write tests in `tests/unit/test_new_step.py`; `tests/unit/test_install_steps.py` checks the registry stays complete
6. Add assertions in the main QEMU tests in `tests/qemu/test_installation.py` (if applicable)

## Adding a Front-end

The setup session decides what to ask, in which order and when; a front-end only renders questions. A new one (a graphical installer, for example) implements the seven methods of `SetupFrontend` in `setup/frontend.py`, then `cli.py` passes it to `run_setup()` instead of the curses one. `tests/unit/test_setup_session.py` drives the session through a scripted front-end, which is the smallest example.

## Idempotent Design

The installer can be run multiple times safely.

This means you can:

- Re-run after a failed installation
- Add packages by modifying config and re-running
- Use the installer as a "converger" to enforce system state

## Testing

### Unit Tests

Fast isolated tests that mock the command runner:

```bash
make test # or: poetry run pytest tests/unit/ -v
```

### QEMU Integration Tests

Full VM-based installation tests:

```bash
# automated tests
make test-qemu ISO=/path/to/archlinux.iso
make test-qemu-full ISO=/path/to/archlinux.iso  # full installation test
```

### Manual QEMU Testing

For interactive testing and debugging, use the manual QEMU test script:

```bash
./tests/qemu/qemu_manual_test.sh [ISO_PATH] [OPTIONS]
```

This script launches a QEMU VM with:

- UEFI Secure Boot in setup mode (the installer enrolls its keys)
- VNC display for visual interaction
- SSH access for command execution
- this working tree copied to `/root/arch_installer`, with its dependencies installed

**Options:**

| Option             | Description                                                       | Default                           |
| ------------------ | ----------------------------------------------------------------- | --------------------------------- |
| `--disk-size`      | Disk size in GB                                                   | 40                                |
| `--memory`         | RAM size in MB                                                    | 4096                              |
| `--work-dir`       | Working directory for VM files                                    | `~/.cache/arch-installer-qemu/manual` |
| `--vnc-port`       | VNC display port offset                                           | 50 (VNC port 5950)                |
| `--ssh-port`       | SSH port forwarding (checked to be free first)                    | 2222                              |
| `--usb-disk [GB]`  | A USB mass storage drive (`/dev/sda`) for the USB boot drive      | 8                                 |
| `--no-iso`         | Start the installed system from the kept disks                    |                                   |
| `--no-copy`        | Do not copy the working tree into the live system                 |                                   |
| `--keep`           | Keep VM files after exit                                          |                                   |
| `--headless`       | Run without VNC display (SSH only)                                |                                   |

The internal disk carries a serial number, so it has a `/dev/disk/by-id` name like a real disk: the USB boot drive needs it.

**Example:**

```bash
# start VM with default settings
./tests/qemu/qemu_manual_test.sh /path/to/archlinux.iso

# with custom options
./tests/qemu/qemu_manual_test.sh /path/to/archlinux.iso --disk-size 60 --memory 8192 --keep
```

**USB boot drive demo:**

```bash
# 1. live system with a USB drive; in the VNC console: cd /root/arch_installer && make run
#    (USB boot drive /dev/sda, recovery ISO /dev/sr0, wipe method secure)
./tests/qemu/qemu_manual_test.sh --usb-disk --keep

# 2. after the installation: poweroff in the VM, press Enter in the script, then start the
#    installed system; it boots from the drive and asks for the passphrase on the console
./tests/qemu/qemu_manual_test.sh --usb-disk --keep --no-iso

# 3. the same disk without the drive: nothing boots
./tests/qemu/qemu_manual_test.sh --keep --no-iso
```

**Access methods:**

```bash
# SSH access (password: root, set by script after boot)
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@localhost -p 2222

# VNC access
# Connect to localhost:5950 (or your configured vnc-port + 5900)
```

**Post-installation steps:**

1. Reboot the VM into the installed system
2. The system will be in secure boot setup mode
3. Enroll your keys with: `sbctl enroll-keys --microsoft`
4. Reboot again - secure boot is now active

## Contributing

Thank you for taking time out of your day to look at this project, PRs are welcomed.
