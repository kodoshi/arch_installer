.PHONY: install run test test-unit test-qemu test-qemu-full lint format verify clean help diagrams encrypt-secrets decrypt-secrets deps init_to_usb backup_to_usb

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
	@echo "  make init_to_usb    - Provision USB drive (partitions + ISO + backup)"
	@echo "  make backup_to_usb  - Update backup on existing USB drive"
	@echo ""
	@echo "  make test           - Run unit tests"
	@echo "  make test-qemu      - Run QEMU tests (set ISO=/path/to/arch.iso)"
	@echo "  make lint           - Check code with ruff"
	@echo "  make format         - Format code with ruff"
	@echo ""
	@echo "Secrets Management:"
	@echo "  make encrypt-secrets ARCH_INSTALLER_SECRETS_KEY=key LUKS_PASSWORD=p1 USER_PASSWORD=p2"
	@echo "       (writes to config.yaml; add NO_WRITE=true to print only)"
	@echo "  make decrypt-secrets (requires ARCH_INSTALLER_SECRETS_KEY env var)"
	@echo ""
	@echo "USB Operations:"
	@echo "  make init_to_usb USB_DEVICE=/dev/sdX [ISO_PATH=/path/to/arch.iso]"
	@echo "       (partitions USB, copies ISO, backs up dotfiles/config/packages)"
	@echo "  make backup_to_usb USB_DEVICE=/dev/sdX [BACKUP_CATEGORIES=dotfiles,keepass]"
	@echo "       (updates backup on existing USB without reformatting)"
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
ISO_PATH ?=
BACKUP_CATEGORIES ?=

init_to_usb:
	@test -n "$(USB_DEVICE)" || (echo "Error: USB_DEVICE required. Usage: make init_to_usb USB_DEVICE=/dev/sdX" && exit 1)
	@echo ">>>>> Initializing USB drive $(USB_DEVICE)..."
	@USB_BOOT_DEVICE=$(USB_DEVICE) ISO_PATH=$(ISO_PATH) BACKUP_CATEGORIES=$(BACKUP_CATEGORIES) VERBOSE=$(VERBOSE) \
		NON_INTERACTIVE=true PYTHONPATH=src python -c 'from arch_installer.cli import usb_init; raise SystemExit(usb_init())'

backup_to_usb:
	@test -n "$(USB_DEVICE)" || (echo "Error: USB_DEVICE required. Usage: make backup_to_usb USB_DEVICE=/dev/sdX" && exit 1)
	@echo ">>>>> Backing up to USB drive $(USB_DEVICE)..."
	@USB_BOOT_DEVICE=$(USB_DEVICE) BACKUP_CATEGORIES=$(BACKUP_CATEGORIES) VERBOSE=$(VERBOSE) \
		NON_INTERACTIVE=true PYTHONPATH=src python -c 'from arch_installer.cli import usb_backup; raise SystemExit(usb_backup())'

clean:
	rm -rf .pytest_cache __pycache__ .coverage htmlcov
	rm -f $(DIAGRAMS_PNG)
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
