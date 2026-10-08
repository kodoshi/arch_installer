.PHONY: install run test test-unit test-qemu test-qemu-full lint format verify clean help diagrams encrypt-secrets decrypt-secrets deps clone_usb_boot backup_to_usb

# secrets given as make arguments are readable by every user through `ps` and stay in the
# shell history, so they are refused; the targets prompt for them without echo instead
SECRET_VARIABLES := ARCH_INSTALLER_SECRETS_KEY LUKS_PASSWORD USER_PASSWORD SOURCE_LUKS_PASSWORD
SECRETS_ON_COMMAND_LINE := $(strip $(foreach variable,$(SECRET_VARIABLES),\
	$(if $(filter command line,$(origin $(variable))),$(variable))))
ifneq ($(SECRETS_ON_COMMAND_LINE),)
$(error $(SECRETS_ON_COMMAND_LINE) must not be given as make arguments: `ps` and the shell history would show them. Leave it out to be prompted, see docs/configuration.md#keeping-secrets-out-of-ps-and-shell-history)
endif

PLANTUML ?= plantuml
VERBOSE ?=
DIAGRAMS_DIR := docs/diagrams
DIAGRAMS_SRC := $(wildcard $(DIAGRAMS_DIR)/*.puml)
DIAGRAMS_PNG := $(DIAGRAMS_SRC:.puml=.png)

help:
	@echo "Declarative ArchLinux Installer (DALI) - Available targets:"
	@echo ""
	@echo "  make deps           - Install dependencies (pacman, live ISO)"
	@echo "  make install        - Run installation (TUI interactive)"
	@echo "                      VERBOSE=true for verbose output, VERBOSE=quiet for minimal"
	@echo "  make verify         - Run post-install verification"
	@echo ""
	@echo "  make clone_usb_boot - Clone the USB boot drive onto a spare drive"
	@echo "  make backup_to_usb  - Back up dotfiles, config and packages to a partition"
	@echo ""
	@echo "  make test           - Run unit tests"
	@echo "  make test-qemu      - Run QEMU tests (set ISO=/path/to/arch.iso)"
	@echo "  make lint           - Check code with ruff"
	@echo "  make format         - Format code with ruff"
	@echo ""
	@echo "Secrets Management (the key and passwords are prompted for, never passed as arguments):"
	@echo "  make encrypt-secrets [CONFIG_PATH=config/config.yaml] [NO_WRITE=true]"
	@echo "       (writes the encrypted passwords to config.yaml; NO_WRITE=true only prints them)"
	@echo "  make decrypt-secrets [CONFIG_PATH=config/config.yaml]"
	@echo ""
	@echo "USB Operations:"
	@echo "  make clone_usb_boot USB_DEVICE=/dev/sdX SPARE_DEVICE=/dev/sdY"
	@echo "       (identical spare: boots and unlocks the system on its own)"
	@echo "  make backup_to_usb BACKUP_PARTITION=/dev/sdY1 [BACKUP_CATEGORIES=dotfiles,keepass]"
	@echo "       (mounts the partition and updates the backup, never formats it)"
	@echo ""
	@echo "Quick start (from Arch live ISO):"
	@echo "  make deps"
	@echo "  git clone https://github.com/kodoshi/arch_installer.git"
	@echo "  cd arch_installer && make install"

deps:
	@echo ">>>>> Installing dependencies with pacman..."
	pacman -Sy --noconfirm --needed glibc python python-yaml python-cryptography python-cffi

install: deps run

run:
	@echo ">>>>> Starting Arch Installer..."
	@VERBOSE=$(VERBOSE) PYTHONPATH=src python -m arch_installer.cli

verify:
	@echo ">>>>> Running installation verification..."
	@bash scripts/verify_install.sh --fix --verbose

test: test-unit

test-unit:
	poetry run pytest tests/unit/ -v

lint:
	poetry run ruff check src tests
	poetry run ruff format --check src tests

format:
	poetry run ruff format src tests
	poetry run ruff check --fix src tests

ISO ?= ./archlinux-x86_64.iso
test-qemu:
	@test -f "$(ISO)" || (echo "Error: ISO not found. Use: make test-qemu ISO=/path/to/arch.iso" && exit 1)
	poetry run pytest tests/qemu/ --arch-iso "$(ISO)" -v

test-qemu-full:
	@test -f "$(ISO)" || (echo "Error: ISO not found. Use: make test-qemu-full ISO=/path/to/arch.iso" && exit 1)
	poetry run pytest tests/qemu/test_installation.py::TestQemuFullInstallation --arch-iso "$(ISO)" -v -s

CONFIG_PATH ?= config/config.yaml
NO_WRITE ?=

# passwords and key reach python through the environment, never through its source
encrypt-secrets:
	@CONFIG_PATH="$(CONFIG_PATH)" NO_WRITE=$(NO_WRITE) PYTHONPATH=src \
		python -c 'from arch_installer.cli import encrypt_secrets; raise SystemExit(encrypt_secrets())'

decrypt-secrets:
	@CONFIG_PATH="$(CONFIG_PATH)" PYTHONPATH=src \
		python -c 'from arch_installer.cli import decrypt_secrets; raise SystemExit(decrypt_secrets())'

diagrams: $(DIAGRAMS_PNG)
	@echo ">>>>> Diagrams generated"

$(DIAGRAMS_DIR)/%.png: $(DIAGRAMS_DIR)/%.puml
	@mkdir -p $(DIAGRAMS_DIR)
	$(PLANTUML) -tpng -o . $<

USB_DEVICE ?=
SPARE_DEVICE ?=
BACKUP_PARTITION ?=
BACKUP_CATEGORIES ?=

clone_usb_boot:
	@test -n "$(USB_DEVICE)" -a -n "$(SPARE_DEVICE)" || (echo "Error: Usage: make clone_usb_boot USB_DEVICE=/dev/sdX SPARE_DEVICE=/dev/sdY" && exit 1)
	@USB_BOOT_DEVICE=$(USB_DEVICE) SPARE_USB_DEVICE=$(SPARE_DEVICE) VERBOSE=$(VERBOSE) \
		PYTHONPATH=src python -c 'from arch_installer.cli import clone_usb_boot; raise SystemExit(clone_usb_boot())'

backup_to_usb:
	@test -n "$(BACKUP_PARTITION)" || (echo "Error: BACKUP_PARTITION required. Usage: make backup_to_usb BACKUP_PARTITION=/dev/sdY1" && exit 1)
	@echo ">>>>> Backing up to $(BACKUP_PARTITION)..."
	@BACKUP_PARTITION=$(BACKUP_PARTITION) BACKUP_CATEGORIES=$(BACKUP_CATEGORIES) VERBOSE=$(VERBOSE) \
		NON_INTERACTIVE=true PYTHONPATH=src python -c 'from arch_installer.cli import usb_backup; raise SystemExit(usb_backup())'

clean:
	rm -rf .pytest_cache __pycache__ .coverage htmlcov
	rm -f $(DIAGRAMS_PNG)
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
