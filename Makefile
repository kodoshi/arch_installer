.PHONY: install gui-install run run-gui test test-unit test-qemu test-qemu-full verify clean help diagrams encrypt-secrets decrypt-secrets deps

PLANTUML ?= plantuml
DIAGRAMS_DIR := docs/diagrams
DIAGRAMS_SRC := $(wildcard $(DIAGRAMS_DIR)/*.puml)
DIAGRAMS_PNG := $(DIAGRAMS_SRC:.puml=.png)

help:
	@echo "Declarative ArchLinux Installer (DALI) - Available targets:"
	@echo ""
	@echo "  make deps        - Install dependencies (pacman, live ISO)"
	@echo "  make install     - CLI installation (interactive)"
	@echo "  make gui-install - GUI installation (Tkinter)"
	@echo "  make verify      - Run post-install verification"
	@echo ""
	@echo "  make test        - Run unit tests"
	@echo "  make test-qemu   - Run QEMU tests (set ISO=/path/to/arch.iso)"
	@echo ""
	@echo "Secrets Management:"
	@echo "  make encrypt-secrets ARCH_INSTALLER_SECRETS_KEY=key LUKS_PASSWORD=p1 USER_PASSWORD=p2"
	@echo "       (writes to config.yaml; add NO_WRITE=true to print only)"
	@echo "  make decrypt-secrets (requires ARCH_INSTALLER_SECRETS_KEY env var)"
	@echo ""
	@echo "Quick start (from Arch live ISO):"
	@echo "  make deps"
	@echo "  git clone https://github.com/kodoshi/arch_installer.git"
	@echo "  cd arch_installer && make gui-install"

deps:
	@echo ">>>>> Installing dependencies with pacman..."
	pacman -Sy --noconfirm python-yaml python-cryptography python-cffi tk

deps-gui: deps
	@echo ">>>>> Setting up X display for GUI..."
	@# create temporary swap to prevent OOM during font cache updates
	@if [ ! -f /tmp/gui_swap ] && [ "$$USER" = "root" ]; then \
		echo ">>>>> Creating temporary swap for GUI..."; \
		dd if=/dev/zero of=/tmp/gui_swap bs=1M count=1024 2>/dev/null || true; \
		chmod 600 /tmp/gui_swap 2>/dev/null || true; \
		mkswap /tmp/gui_swap 2>/dev/null || true; \
		swapon /tmp/gui_swap 2>/dev/null || true; \
	fi
	@if [ -z "$$DISPLAY" ] && [ -z "$$WAYLAND_DISPLAY" ]; then \
		echo ">>>>> Starting Xvfb virtual display..."; \
		pacman -Sy --noconfirm xorg-server-xvfb xorg-xauth 2>/dev/null || true; \
		pkill -9 -f "Xvfb :99" 2>/dev/null || true; \
		rm -f /tmp/.X99-lock 2>/dev/null || true; \
		sleep 1; \
		Xvfb :99 -screen 0 1024x768x16 & \
		export DISPLAY=:99; \
		echo ">>>>> Virtual display started on :99"; \
	fi

install: deps run

gui-install: deps-gui run-gui

run:
	@echo ">>>>> Starting Arch Installer..."
	@PYTHONPATH=src python -m arch_installer.installer

run-gui:
	@echo ">>>>> Starting Arch Installer (GUI)..."
	@if [ -z "$$DISPLAY" ] && [ -z "$$WAYLAND_DISPLAY" ]; then \
		echo ">>>>> No display found, starting Xvfb..."; \
		pkill -9 -f "Xvfb :99" 2>/dev/null || true; \
		rm -f /tmp/.X99-lock 2>/dev/null || true; \
		sleep 1; \
		Xvfb :99 -screen 0 1024x768x16 & \
		sleep 1; \
		DISPLAY=:99 PYTHONPATH=src python -c "from arch_installer.gui import main; main()"; \
	else \
		PYTHONPATH=src python -c "from arch_installer.gui import main; main()"; \
	fi

verify:
	@echo ">>>>> Running installation verification..."
	PYTHONPATH=src python -m arch_installer.verify --fix --verbose || true;

test: test-unit

test-unit:
	poetry run pytest tests/unit/ -v

ISO ?= ./archlinux-x86_64.iso
test-qemu:
	@test -f "$(ISO)" || (echo "Error: ISO not found. Use: make test-qemu ISO=/path/to/arch.iso" && exit 1)
	poetry run pytest tests/qemu/ --arch-iso "$(ISO)" -v

test-qemu-full:
	@test -f "$(ISO)" || (echo "Error: ISO not found. Use: make test-qemu-full ISO=/path/to/arch.iso" && exit 1)
	poetry run pytest tests/qemu/test_installation.py::TestQemuFullInstallation --arch-iso "$(ISO)" -v -s

ARCH_INSTALLER_SECRETS_KEY ?=
LUKS_PASSWORD ?=
USER_PASSWORD ?=
CONFIG_PATH ?= config/config.yaml
NO_WRITE ?=

encrypt-secrets:
	@test -n "$(ARCH_INSTALLER_SECRETS_KEY)" || (echo "Error: ARCH_INSTALLER_SECRETS_KEY required" && exit 1)
	@test -n "$(LUKS_PASSWORD)" || test -n "$(USER_PASSWORD)" || (echo "Error: No passwords provided" && exit 1)
	@PYTHONPATH=src python -c "\
import yaml, os, sys; \
from arch_installer.core.secrets import encrypt_secret; \
luks = '$(LUKS_PASSWORD)'; user = '$(USER_PASSWORD)'; key = '$(ARCH_INSTALLER_SECRETS_KEY)'; \
no_write = '$(NO_WRITE)'.lower() in ('true', '1', 'yes'); \
config_path = '$(CONFIG_PATH)'; \
luks_enc = encrypt_secret(luks, key) if luks else ''; \
user_enc = encrypt_secret(user, key) if user else ''; \
print('luks_password_encrypted:', repr(luks_enc) if luks_enc else 'N/A'); \
print('user_password_encrypted:', repr(user_enc) if user_enc else 'N/A'); \
if no_write: \
    print('(no-write mode - config.yaml not modified)'); sys.exit(0); \
if not os.path.exists(config_path): \
    print(f'Warning: {config_path} not found - not writing'); sys.exit(0); \
with open(config_path) as f: cfg = yaml.safe_load(f); \
if 'secrets' not in cfg: cfg['secrets'] = {}; \
if luks_enc: cfg['secrets']['luks_password_encrypted'] = luks_enc; \
if user_enc: cfg['secrets']['user_password_encrypted'] = user_enc; \
with open(config_path, 'w') as f: yaml.dump(cfg, f, default_flow_style=False, sort_keys=False); \
print(f'Updated secrets in {config_path}');"

decrypt-secrets:
	@test -n "$$ARCH_INSTALLER_SECRETS_KEY" || (echo "Error: ARCH_INSTALLER_SECRETS_KEY env var required" && exit 1)
	@PYTHONPATH=src python -c "\
import yaml, os; \
from arch_installer.core.secrets import decrypt_secret; \
config_path = '$(CONFIG_PATH)' or 'config/config.yaml'; \
cfg = yaml.safe_load(open(config_path)); \
secrets = cfg.get('secrets', {}); \
key = os.environ['ARCH_INSTALLER_SECRETS_KEY']; \
print('LUKS:', decrypt_secret(secrets.get('luks_password_encrypted', ''), key) or 'N/A'); \
print('User:', decrypt_secret(secrets.get('user_password_encrypted', ''), key) or 'N/A');"

diagrams: $(DIAGRAMS_PNG)
	@echo ">>>>> Diagrams generated"

$(DIAGRAMS_DIR)/%.png: $(DIAGRAMS_DIR)/%.puml
	@mkdir -p $(DIAGRAMS_DIR)
	$(PLANTUML) -tpng -o . $<

clean:
	rm -rf .pytest_cache __pycache__ .coverage htmlcov
	rm -f $(DIAGRAMS_PNG)
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
