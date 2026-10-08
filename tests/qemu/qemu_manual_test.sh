#!/usr/bin/env bash
# Manual QEMU testing script for arch_installer
#
# This script launches a QEMU VM with:
# - UEFI Secure Boot in setup mode (the installer enrolls its keys)
# - VNC display for visual interaction
# - SSH access for command execution
# - this working tree copied to /root/arch_installer, with its dependencies installed
#
# Usage:
#   tests/qemu/qemu_manual_test.sh [ISO_PATH] [OPTIONS]
#
# Options:
#   --disk-size SIZE     Disk size in GB (default: 40)
#   --memory SIZE        RAM size in MB (default: 4096)
#   --work-dir DIR       Working directory for VM files
#                        (default: ~/.cache/arch-installer-qemu/manual, not /tmp: often tmpfs)
#   --vnc-port PORT      VNC display port offset (default: 50, so VNC port 5950)
#   --ssh-port PORT      SSH port forwarding (default: 2222)
#   --usb-disk [SIZE]    Add a USB mass storage drive (/dev/sda in the VM) for the
#                        USB boot drive (default size: 8GB)
#   --no-iso             Start the installed system from the disks in the work directory
#                        (use with --keep on the run that installed it)
#   --no-copy            Do not copy this working tree into the live system
#   --keep               Keep VM files after exit
#   --headless           Run without VNC display (SSH only)
#
# Requirements:
#   - qemu-full (qemu-system-x86_64), edk2-ovmf (UEFI firmware with Secure Boot)
#   - socat, nc (openbsd-netcat), sshpass
#
# Demo:
#   1. tests/qemu/qemu_manual_test.sh --usb-disk --keep
#   2. in the VNC console: cd /root/arch_installer && make run
#      (USB boot drive: /dev/sda, recovery ISO: /dev/sr0, wipe method: secure)
#   3. after the installation: poweroff, then press Enter here
#   4. tests/qemu/qemu_manual_test.sh --usb-disk --keep --no-iso
#      boots from the USB drive and asks for the LUKS passphrase on the console;
#      without --usb-disk nothing boots
#
# SSH access (during live ISO):
#   ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@localhost -p 2222
#   Password: root (set by the script after boot)
#
# VNC access:
#   Connect to localhost:5950 (or your configured port)

set -euo pipefail

# default configuration
ARCH_ISO="$HOME/Downloads/archlinux.iso"
DISK_SIZE_GB=40
MEMORY_MB=4096
CPUS=4
# not /tmp: it is usually a RAM-backed tmpfs and the disk image grows to its full size
WORK_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/arch-installer-qemu/manual"
VNC_PORT=50
SSH_PORT=2222
MONITOR_PORT=4444
KEEP_FILES=false
HEADLESS=false
USB_DISK=false
USB_DISK_SIZE_GB=8
BOOT_ISO=true
COPY_REPOSITORY=true
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSH_OPTIONS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
    -o ConnectTimeout=5 -o PasswordAuthentication=yes -o PubkeyAuthentication=no)

# ANSI colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
print_success() { echo -e "${GREEN}[OK]${NC} $*"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $*"; }
print_error() { echo -e "${RED}[ERROR]${NC} $*"; }

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

# parse arguments
# only shift if first argument doesn't look like an option (it's the ISO path)
if [[ $# -gt 0 && ! "$1" =~ ^-- ]]; then
    ARCH_ISO="$1"
    shift
fi

while [[ $# -gt 0 ]]; do
    case $1 in
        --disk-size)
            DISK_SIZE_GB="$2"
            shift 2
            ;;
        --memory)
            MEMORY_MB="$2"
            shift 2
            ;;
        --work-dir)
            WORK_DIR="$2"
            shift 2
            ;;
        --vnc-port)
            VNC_PORT="$2"
            shift 2
            ;;
        --ssh-port)
            SSH_PORT="$2"
            shift 2
            ;;
        --usb-disk)
            USB_DISK=true
            # accept optional size argument (next arg if it's a number)
            if [[ $# -gt 1 && "$2" =~ ^[0-9]+$ ]]; then
                USB_DISK_SIZE_GB="$2"
                shift
            fi
            shift
            ;;
        --no-iso)
            BOOT_ISO=false
            shift
            ;;
        --no-copy)
            COPY_REPOSITORY=false
            shift
            ;;
        --keep)
            KEEP_FILES=true
            shift
            ;;
        --headless)
            HEADLESS=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            print_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# check requirements
check_requirements() {
    local missing=()

    if ! command -v qemu-system-x86_64 &>/dev/null; then
        missing+=("qemu-system-x86_64 (install qemu-full)")
    fi

    if ! command -v qemu-img &>/dev/null; then
        missing+=("qemu-img (install qemu-full)")
    fi

    local tool
    for tool in socat nc sshpass; do
        command -v "$tool" &>/dev/null || missing+=("$tool")
    done

    # check for OVMF files
    local ovmf_code=""
    local ovmf_vars=""

    # try secure boot variants first (required for setup mode)
    local ovmf_paths=(
        "/usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.4m.fd:/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"
        "/usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.fd:/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"
        "/usr/share/OVMF/OVMF_CODE.fd:/usr/share/OVMF/OVMF_VARS.fd"
        "/usr/share/edk2/ovmf/OVMF_CODE.fd:/usr/share/edk2/ovmf/OVMF_VARS.fd"
    )

    for pair in "${ovmf_paths[@]}"; do
        local code="${pair%%:*}"
        local vars="${pair##*:}"
        if [[ -f "$code" && -f "$vars" ]]; then
            ovmf_code="$code"
            ovmf_vars="$vars"
            break
        fi
    done

    if [[ -z "$ovmf_code" ]]; then
        missing+=("OVMF firmware (install edk2-ovmf)")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        print_error "Missing requirements:"
        for requirement in "${missing[@]}"; do
            echo "  - $requirement"
        done
        exit 1
    fi

    # export for use in build_qemu_command
    export OVMF_CODE="$ovmf_code"
    export OVMF_VARS="$ovmf_vars"
}

# QEMU only reports that it could not forward the port, so check it first
check_ssh_port() {
    if nc -z localhost "$SSH_PORT" 2>/dev/null; then
        print_error "Port $SSH_PORT is already in use on this host: choose another with --ssh-port"
        exit 1
    fi
}

# check if ISO exists
check_iso() {
    if [[ "$BOOT_ISO" != "true" ]]; then
        return 0
    fi
    if [[ ! -f "$ARCH_ISO" ]]; then
        print_error "ISO file not found: $ARCH_ISO"
        echo "Download from: https://archlinux.org/download/"
        exit 1
    fi
    print_success "Found ISO: $ARCH_ISO"
}

# set up working directory
setup_work_dir() {
    print_info "Setting up work directory: $WORK_DIR"
    if [[ "$BOOT_ISO" != "true" && ! -f "$WORK_DIR/disk.qcow2" ]]; then
        print_error "--no-iso starts the disks in $WORK_DIR: install onto them first (with --keep)"
        exit 1
    fi
    mkdir -p "$WORK_DIR"

    # create disk image if doesn't exist
    if [[ ! -f "$WORK_DIR/disk.qcow2" ]]; then
        print_info "Creating ${DISK_SIZE_GB}GB disk image..."
        qemu-img create -f qcow2 "$WORK_DIR/disk.qcow2" "${DISK_SIZE_GB}G"
    else
        print_info "Using existing disk image"
    fi


    # the UEFI variables hold the enrolled Secure Boot keys, so a kept VM keeps its copy
    if [[ ! -f "$WORK_DIR/OVMF_VARS.fd" ]]; then
        print_info "Setting up UEFI firmware..."
        cp "$OVMF_VARS" "$WORK_DIR/OVMF_VARS.fd"
    else
        print_info "Using existing UEFI variables (enrolled keys are kept)"
    fi

    # create USB disk image if requested
    if [[ "$USB_DISK" == "true" ]]; then
        if [[ ! -f "$WORK_DIR/usb_disk.qcow2" ]]; then
            print_info "Creating ${USB_DISK_SIZE_GB}GB USB disk image (will appear as /dev/sda)..."
            qemu-img create -f qcow2 "$WORK_DIR/usb_disk.qcow2" "${USB_DISK_SIZE_GB}G"
        else
            print_info "Using existing USB disk image"
        fi
    fi

    print_success "Work directory ready"
}

# build QEMU command
build_qemu_command() {
    local qemu_arguments=(
        qemu-system-x86_64
        -machine "q35,smm=on"
        -smp "$CPUS"
        -m "$MEMORY_MB"
    )

    # enable KVM if available; the host CPU model needs it
    if [[ -r /dev/kvm ]]; then
        qemu_arguments+=("-enable-kvm" "-cpu" "host")
        print_success "KVM acceleration enabled" >&2
    else
        qemu_arguments+=("-cpu" "max")
        print_warning "KVM not available, running in emulation mode (slow)" >&2
    fi

    # UEFI firmware with secure boot in setup mode
    # using pflash for proper UEFI variable storage
    qemu_arguments+=(
        -global "driver=cfi.pflash01,property=secure,value=on"
        -drive "if=pflash,format=raw,unit=0,file=$OVMF_CODE,readonly=on"
        -drive "if=pflash,format=raw,unit=1,file=$WORK_DIR/OVMF_VARS.fd"
    )

    # disk; its serial number gives it a /dev/disk/by-id name, as a real disk has, which
    # the USB boot drive needs to find a disk without a partition table
    qemu_arguments+=(
        -drive "if=none,id=disk,file=$WORK_DIR/disk.qcow2,format=qcow2"
        -device "virtio-blk-pci,drive=disk,serial=dali-demo-disk"
    )

    # USB drive on an xHCI controller, removable like a stick (USB boot drive testing);
    # without the ISO the firmware starts from it first
    if [[ "$USB_DISK" == "true" ]]; then
        local boot_order=""
        [[ "$BOOT_ISO" != "true" ]] && boot_order=",bootindex=1"
        qemu_arguments+=(
            -device qemu-xhci,id=xhci
            -drive "if=none,id=usb-disk,file=$WORK_DIR/usb_disk.qcow2,format=qcow2"
            -device "usb-storage,bus=xhci.0,drive=usb-disk,removable=on$boot_order"
        )
        print_info "USB drive attached as /dev/sda (${USB_DISK_SIZE_GB}GB)" >&2
    fi

    # CD-ROM with ISO
    if [[ "$BOOT_ISO" == "true" ]]; then
        qemu_arguments+=(
            -cdrom "$ARCH_ISO"
            -boot "d"
        )
    fi

    # networking with SSH port forward
    qemu_arguments+=(
        -netdev "user,id=net0,hostfwd=tcp::${SSH_PORT}-:22"
        -device "virtio-net-pci,netdev=net0"
    )

    # serial console on socket for interactive access and logging
    qemu_arguments+=(
        -chardev "socket,id=serial0,path=$WORK_DIR/serial.sock,server=on,wait=off,logfile=$WORK_DIR/serial.log"
        -serial "chardev:serial0"
    )

    # QEMU monitor on socket
    qemu_arguments+=(
        -monitor "unix:$WORK_DIR/monitor.sock,server,nowait"
    )

    # display
    if [[ "$HEADLESS" == "true" ]]; then
        qemu_arguments+=("-display" "none")
    else
        qemu_arguments+=("-vnc" "127.0.0.1:${VNC_PORT}")
    fi

    # run in background (daemonize)
    qemu_arguments+=("-daemonize")

    echo "${qemu_arguments[@]}"
}

# clean up on exit
cleanup() {
    # stop QEMU if running
    if [[ -S "$WORK_DIR/monitor.sock" ]]; then
        print_info "Stopping QEMU..."
        echo "quit" | socat - "UNIX-CONNECT:$WORK_DIR/monitor.sock" >/dev/null 2>&1 || true
        sleep 1
    fi

    if [[ "$KEEP_FILES" != "true" ]]; then
        print_info "Cleaning up work directory..."
        rm -rf "$WORK_DIR"
    else
        print_info "Keeping work directory: $WORK_DIR"
    fi
}

# send command via QEMU monitor sendkey (types into VM virtual keyboard)
send_console_command() {
    local text="$1"
    local wait_after="${2:-1}"
    local socket="$WORK_DIR/monitor.sock"

    if [[ ! -S "$socket" ]]; then
        print_error "Monitor socket not found"
        return 1
    fi

    declare -A key_map=(
        [" "]="spc"
        ["-"]="minus"
        ["="]="equal"
        ["["]="bracket_left"
        ["]"]="bracket_right"
        [";"]="semicolon"
        ["'"]="apostrophe"
        ["\\"]="backslash"
        [","]="comma"
        ["."]="dot"
        ["/"]="slash"
        ["\`"]="grave_accent"
        ["!"]="shift-1"
        ["@"]="shift-2"
        ["#"]="shift-3"
        ["$"]="shift-4"
        ["%"]="shift-5"
        ["^"]="shift-6"
        ["&"]="shift-7"
        ["*"]="shift-8"
        ["("]="shift-9"
        [")"]="shift-0"
        ["_"]="shift-minus"
        ["+"]="shift-equal"
        ["{"]="shift-bracket_left"
        ["}"]="shift-bracket_right"
        [":"]="shift-semicolon"
        ["\""]="shift-apostrophe"
        ["|"]="shift-backslash"
        ["<"]="shift-comma"
        [">"]="shift-dot"
        ["?"]="shift-slash"
        ["~"]="shift-grave_accent"
    )

    # build the list of commands to send
    local commands=""
    for (( position=0; position<${#text}; position++ )); do
        local character="${text:$position:1}"
        local key=""

        if [[ -n "${key_map[$character]:-}" ]]; then
            key="${key_map[$character]}"
        elif [[ "$character" =~ [A-Z] ]]; then
            key="shift-${character,,}"  # lowercase with shift
        else
            key="$character"
        fi

        commands+="sendkey $key"$'\n'
    done

    # add enter at the end
    commands+="sendkey ret"$'\n'

    # send all commands to the monitor socket with small delays
    echo "$commands" | while IFS= read -r line; do
        echo "$line" | socat - "UNIX-CONNECT:$socket" >/dev/null 2>&1
        sleep 0.05
    done

    sleep "$wait_after"
}

# the forwarded port opens as soon as QEMU starts, so wait for the guest's sshd itself:
# it answers with a password prompt (sshpass exit code 5) once the live system is up
wait_for_live_system() {
    local deadline=$((SECONDS + 300))
    local exit_code

    print_info "Waiting for the live system's sshd..."
    while [[ $SECONDS -lt $deadline ]]; do
        exit_code=0
        sshpass -p root ssh "${SSH_OPTIONS[@]}" -p "$SSH_PORT" root@localhost true \
            &>/dev/null || exit_code=$?
        if [[ $exit_code -eq 0 || $exit_code -eq 5 ]]; then
            print_success "sshd is up"
            return 0
        fi
        sleep 3
    done

    print_warning "Timeout waiting for sshd"
    return 1
}

run_in_vm() {
    sshpass -p root ssh "${SSH_OPTIONS[@]}" -p "$SSH_PORT" root@localhost "$@"
}

# set up root password via QEMU monitor sendkey on the autologin console
setup_root_password() {
    local attempt

    print_info "Setting the root password on the console..."
    for attempt in 1 2 3 4 5; do
        send_console_command "echo root:root | chpasswd" 2
        if run_in_vm true &>/dev/null; then
            print_success "Root password set to 'root', SSH works"
            return 0
        fi
        sleep 5
    done

    print_warning "Could not log in over SSH: set the root password on the console yourself"
    return 1
}

# expand cowspace for package installations
expand_cowspace() {
    if run_in_vm "mount -o remount,size=2G /run/archiso/cowspace" 2>/dev/null; then
        print_success "Cowspace expanded to 2G"
    else
        print_warning "Failed to expand cowspace - some packages may fail to install"
    fi
}

# the working tree, not the published repository: the demo shows the code as it is here
copy_repository() {
    print_info "Copying $REPOSITORY_ROOT to /root/arch_installer..."
    if git -C "$REPOSITORY_ROOT" ls-files --cached --others --exclude-standard -z \
        | tar -C "$REPOSITORY_ROOT" --null -T - -czf - \
        | run_in_vm "mkdir -p /root/arch_installer && tar -xzf - -C /root/arch_installer"; then
        print_success "Installer copied"
    else
        print_warning "Copying the installer failed"
        return 1
    fi

    # an older ISO upgrades into package splits (libgcc out of gcc-libs) that conflict
    # with its own files, so they are overwritten, as the QEMU tests do
    print_info "Installing the installer's dependencies..."
    if run_in_vm "pacman-key --init >/dev/null 2>&1; pacman -Sy --noconfirm --needed \
        --overwrite '*' glibc python python-yaml python-cryptography python-cffi make" \
        >/dev/null 2>&1; then
        print_success "Dependencies installed"
    else
        print_warning "Installing the dependencies failed: run 'make deps' in the VM"
    fi
}

# print connection info
print_connection_info() {
    echo ""
    echo "=============================================="
    echo "          QEMU VM STARTED"
    echo "=============================================="
    echo ""
    echo "Connection information:"
    echo ""
    if [[ "$HEADLESS" != "true" ]]; then
        echo "  VNC:  vnc://localhost:$((5900 + VNC_PORT))"
        echo "        vncviewer localhost:$((5900 + VNC_PORT))"
        echo ""
    fi
    echo "  SSH:  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@localhost -p $SSH_PORT"
    echo "        password: root"
    if [[ "$USB_DISK" == "true" ]]; then
        echo ""
        echo "  USB:  /dev/sda (${USB_DISK_SIZE_GB}GB) - USB boot drive /dev/sda, recovery ISO /dev/sr0, wipe method secure"
    fi
    echo ""
}

main() {
    print_info "QEMU Manual Test for Arch Installer"
    echo ""

    check_requirements
    check_ssh_port
    check_iso
    setup_work_dir

    trap cleanup EXIT

    local qemu_command_line
    qemu_command_line=$(build_qemu_command)

    print_info "Starting QEMU..."
    echo "Command: $qemu_command_line"
    echo ""

    # run QEMU (daemonizes itself)
    eval "$qemu_command_line"

    # wait for serial socket to be ready
    sleep 2

    print_connection_info

    if [[ "$BOOT_ISO" == "true" ]]; then
        # prepare the live system: SSH, room for packages, the installer
        if wait_for_live_system && setup_root_password; then
            expand_cowspace
            if [[ "$COPY_REPOSITORY" == "true" ]]; then
                copy_repository
            fi
            echo ""
            print_info "In the VNC console: cd /root/arch_installer && make run"
        fi
    else
        print_info "Starting the installed system: the LUKS passphrase prompt is on the VNC console"
    fi

    echo ""
    if [[ "$KEEP_FILES" == "true" ]]; then
        print_success "VM is running. Press Enter to stop it (files are kept), or Ctrl+C."
    else
        print_success "VM is running. Press Enter to stop it and delete its files, or Ctrl+C."
    fi
    echo ""

    # wait for user input
    read -r

    print_info "Shutting down..."
}

main "$@"
