# DALI - Code Structure Analysis

## Class Descriptions and Responsibilities

### Entry Points

| Class/Function                        | Description             | Responsibility                                          |
| ------------------------------------- | ----------------------- | ------------------------------------------------------- |
| `installer.main()`                    | CLI entry point         | Create ArchInstaller from env/config, run installation  |
| `gui.main()`                          | GUI entry point         | Launch tkinter wizard, create ArchInstaller             |
| `create_installer_from_env()`         | Factory function        | Determine interactive/non-interactive, route to builder |
| `_create_installer_interactive()`     | Interactive builder     | Run prompts, collect selections, create installer       |
| `_create_installer_non_interactive()` | Non-interactive builder | Read env vars/config, create installer                  |

### Core Orchestration

| Class            | Description                    | Responsibility                                         |
| ---------------- | ------------------------------ | ------------------------------------------------------ |
| `ArchInstaller`  | Main orchestrator              | Sequence steps, manage state, delegate to step classes |
| `InstallOptions` | Installation options dataclass | Store user selections for installation behavior        |
| `InstallStep`    | Step enum                      | Define available installation steps                    |
| `StepDefinition` | Step metadata                  | Name, description, required flag for each step         |

### Configuration Layer

| Class                     | Description             | Responsibility                                        |
| ------------------------- | ----------------------- | ----------------------------------------------------- |
| `DeclaredConfig`          | Root config container   | Immutable aggregation of all config sections          |
| `RuntimeConfig`           | Runtime state container | Mutable state populated during installation           |
| `SystemConfig`            | System settings         | hostname, timezone, locale, user, mirrors, cpu_vendor |
| `StorageConfig`           | Storage settings        | target_disk, efi_size, luks, btrfs, swap              |
| `BootConfig`              | Boot settings           | kernels, variants, cmdline, loader, secure_boot       |
| `PackagesConfig`          | Package settings        | profile, base packages, desktops                      |
| `GpuConfig`               | GPU settings            | vendor, driver, driver packages                       |
| `SnapperConfig`           | Snapper settings        | enabled, volumes, retention                           |
| `FirewallConfig`          | Firewall settings       | enabled, rules, SSH config                            |
| `DockerConfig`            | Docker settings         | enabled, storage driver, access group                 |
| `DotfilesConfig`          | Dotfiles settings       | enabled, remote URL, repo path                        |
| `MigrationConfig`         | Migration settings      | enabled, source disk, preserve flags                  |
| `EncryptedSecretsConfig`  | Encrypted passwords     | LUKS and user passwords encrypted                     |
| `load_main_yaml_config()` | YAML loader             | Parse config.yaml, instantiate DeclaredConfig         |
| `_parse_*_section()`      | Section parsers         | Parse each YAML section with defaults                 |

### User Interaction Layer

| Class                    | Description         | Responsibility                             |
| ------------------------ | ------------------- | ------------------------------------------ |
| `InteractionStrategy`    | Abstract interface  | Define contract for user interaction       |
| `CLIInteractionStrategy` | CLI implementation  | Terminal prompts with config.yaml defaults |
| `GUIInteractionStrategy` | GUI implementation  | Tkinter screens for wizard flow            |
| `HardwareDetector`       | Hardware info       | List disks, get RAM size via lsblk         |
| `InstallationSelections` | Selection container | Store all user choices from prompts        |
| `MenuOption`             | Menu item           | value + description for menus              |
| `DiskInfo`               | Disk metadata       | name, path, model, size, partitions        |

### Installation Steps

| Class                  | Description          | Responsibility                            |
| ---------------------- | -------------------- | ----------------------------------------- |
| `StorageProvisioner`   | Storage setup        | Wipe, partition, LUKS, BTRFS, mount, swap |
| `PackageInstaller`     | Package installation | pacstrap, package lists, AUR              |
| `SystemConfigurator`   | System config        | hostname, users, fstab, locale, etc       |
| `GpuDriverSetup`       | GPU setup            | Install GPU driver packages               |
| `UkiGenerator`         | UKI generation       | mkinitcpio, cmdline, sign UKIs            |
| `BootloaderSetup`      | Bootloader           | systemd-boot, sbctl, key enrollment       |
| `SnapperSetup`         | Snapper config       | Create configs, retention, snap-pac       |
| `FirewallSetup`        | Firewall             | UFW rules, SSH access                     |
| `InstallationMigrator` | Migration            | Backup/restore from old installation      |
| `WipeMethod`           | Wipe enum            | QUICK, SECURE, DISCARD, SKIP              |

### Command Execution

| Class                 | Description      | Responsibility                     |
| --------------------- | ---------------- | ---------------------------------- |
| `CommandRunner`       | Abstract runner  | Interface for command execution    |
| `SystemCommandRunner` | System runner    | subprocess execution with logging  |
| `CommandResult`       | Result container | exit_code, stdout, stderr, success |

---

## Identified Redundancies and Overlaps

### 1. InstallOptions vs RuntimeConfig (OVERLAP)

**Problem**: Both store similar flags and values.

- `InstallOptions`: enable_snapshot_boot, enable_firewall, enable_hibernation, enable_docker, skip_swap, target_disk, luks_password, swap_size_mb, cpu_vendor, gpu_vendor
- `RuntimeConfig`: same fields + more

**Solution**: `InstallOptions` should ONLY contain install-time options that differ from config.
`RuntimeConfig` should be the single source of truth after options are applied.
Currently `_apply_options_to_state()` copies from InstallOptions to RuntimeConfig - this is acceptable
but could be simplified by using RuntimeConfig directly from prompts.

### 2. SWAP_OPTIONS hardcoded values (ISSUE)

**Problem**: `SWAP_OPTIONS` in `interaction.py` has hardcoded values like 8192, 16384, 32768, 65536.
The default should come from config.yaml but currently `prompt_swap_size()` doesn't pass the default to `_get_selection()`.

**Location**: `cli_interaction.py:419-420`

```python
_print_menu("Swap Size:", SWAP_OPTIONS)
selection = _get_selection(SWAP_OPTIONS, "Select swap size")  # MISSING default_value!
```

### 3. \_build_final_config_dict is static (ISSUE)

**Problem**: The method builds a static dict instead of dynamically taking existing config and only overwriting changed values.

**Location**: `installer.py:421-470`

### 4. Configuration Summary incomplete (ISSUE)

**Problem**: `_show_summary()` in CLI doesn't show all important fields.
**Location**: `cli_interaction.py:557-585`

### 5. Wipe method not exposed in Python (MISSING)

**Problem**: `scripts/storage.sh` has wipe method selection (QUICK, SECURE, DISCARD, SKIP) but
Python's `StorageProvisioner` only uses `WipeMethod.QUICK` or `WipeMethod.SKIP` hardcoded.
No interactive prompt for wipe method in CLI/GUI.

**Location**: `installer.py:305` - hardcodes wipe method based on migration flag

### 6. Makefile Xvfb error (BUG)

**Problem**: `Xvfb :99` is started without checking if display :99 is already in use.
**Location**: `Makefile:38, 55`

---

## Recommendations for Code Reduction

1. **Merge overlapping prompt code**: CLI and GUI have similar prompt logic. Extract common validation.

2. **Simplify config building**: Make `_build_final_config_dict` dynamic by reading from DeclaredConfig and overlaying RuntimeConfig changes.

3. **Add wipe method to config.yaml and prompts**: Extend `StorageConfig` with wipe_method field.

4. **Fix Xvfb**: Check for existing display before starting.

5. **Pass config defaults to all prompts**: Ensure Enter = config.yaml default everywhere.
