#!/usr/bin/env bash
# Manual QEMU testing script for arch_installer
#
# This script launches a QEMU VM with:
# - UEFI secure boot in setup mode (keys can be enrolled after install)
# - VNC display for visual interaction
# - SSH access for command execution
#
# Usage:
#   ./scripts/qemu_manual_test.sh [ISO_PATH] [OPTIONS]
#
# Options:
#   --disk-size SIZE     Disk size in GB (default: 40)
#   --memory SIZE        RAM size in MB (default: 4096)
#   --work-dir DIR       Working directory for VM files (default: /tmp/qemu-manual-test)
#   --vnc-port PORT      VNC display port offset (default: 50, so VNC port 5950)
#   --ssh-port PORT      SSH port forwarding (default: 2222)
#   --keep               Keep VM files after exit
#   --headless           Run without VNC display (SSH only)
#
# Requirements:
#   - qemu-full (qemu-system-x86_64)
#   - edk2-ovmf (UEFI firmware with secure boot support)
#
# After installation completes:
#   1. Reboot the VM into the installed system
#   2. The system will be in secure boot setup mode
#   3. Enroll your keys with: sbctl enroll-keys --microsoft
#   4. Reboot again - secure boot is now active
#
# SSH access (during live ISO):
#   ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@localhost -p 2222
#   Password: root (set by the script after boot)
#
# VNC access:
#   Connect to localhost:5950 (or your configured port)

set -euo pipefail

# default configuration
ARCH_ISO="/home/USER/Downloads/archlinux.iso"
DISK_SIZE_GB=40
MEMORY_MB=4096
CPUS=4
WORK_DIR="/tmp/qemu-manual-test"
VNC_PORT=50
SSH_PORT=2222
MONITOR_PORT=4444
KEEP_FILES=false
HEADLESS=false

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
    head -n 40 "$0" | tail -n +2 | sed 's/^# //' | sed 's/^#//'
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
        for req in "${missing[@]}"; do
            echo "  - $req"
        done
        exit 1
    fi

    # export for use in build_qemu_command
    export OVMF_CODE="$ovmf_code"
    export OVMF_VARS="$ovmf_vars"
}

# check if ISO exists
check_iso() {
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
    mkdir -p "$WORK_DIR"

    # create disk image if doesn't exist
    if [[ ! -f "$WORK_DIR/disk.qcow2" ]]; then
        print_info "Creating ${DISK_SIZE_GB}GB disk image..."
        qemu-img create -f qcow2 "$WORK_DIR/disk.qcow2" "${DISK_SIZE_GB}G"
    else
        print_info "Using existing disk image"
    fi

    # copy OVMF vars (needs to be writable for secure boot)
    print_info "Setting up UEFI firmware..."
    cp "$OVMF_VARS" "$WORK_DIR/OVMF_VARS.fd"

    print_success "Work directory ready"
}

# build QEMU command
build_qemu_command() {
    local cmd=(
        qemu-system-x86_64
        -machine "q35,smm=on"
        -cpu "host"
        -smp "$CPUS"
        -m "$MEMORY_MB"
    )

    # enable KVM if available
    if [[ -r /dev/kvm ]]; then
        cmd+=("-enable-kvm")
        print_success "KVM acceleration enabled" >&2
    else
        print_warning "KVM not available, running in emulation mode (slow)" >&2
    fi

    # UEFI firmware with secure boot in setup mode
    # using pflash for proper UEFI variable storage
    cmd+=(
        -global "driver=cfi.pflash01,property=secure,value=on"
        -drive "if=pflash,format=raw,unit=0,file=$OVMF_CODE,readonly=on"
        -drive "if=pflash,format=raw,unit=1,file=$WORK_DIR/OVMF_VARS.fd"
    )

    # disk
    cmd+=(
        -drive "file=$WORK_DIR/disk.qcow2,format=qcow2,if=virtio"
    )

    # CD-ROM with ISO
    cmd+=(
        -cdrom "$ARCH_ISO"
        -boot "d"
    )

    # networking with SSH port forward
    cmd+=(
        -netdev "user,id=net0,hostfwd=tcp::${SSH_PORT}-:22"
        -device "virtio-net-pci,netdev=net0"
    )

    # serial console on socket for interactive access and logging
    cmd+=(
        -chardev "socket,id=serial0,path=$WORK_DIR/serial.sock,server=on,wait=off,logfile=$WORK_DIR/serial.log"
        -serial "chardev:serial0"
    )

    # QEMU monitor on socket
    cmd+=(
        -monitor "unix:$WORK_DIR/monitor.sock,server,nowait"
    )

    # display
    if [[ "$HEADLESS" == "true" ]]; then
        cmd+=("-display" "none")
    else
        cmd+=("-vnc" "127.0.0.1:${VNC_PORT}")
    fi

    # run in background (daemonize)
    cmd+=("-daemonize")

    echo "${cmd[@]}"
}

# clean up on exit
cleanup() {
    # stop QEMU if running
    if [[ -S "$WORK_DIR/monitor.sock" ]]; then
        print_info "Stopping QEMU..."
        echo "quit" | socat - "UNIX-CONNECT:$WORK_DIR/monitor.sock" 2>/dev/null || true
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
    local cmd="$1"
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
    for (( i=0; i<${#cmd}; i++ )); do
        local char="${cmd:$i:1}"
        local key=""

        if [[ -n "${key_map[$char]:-}" ]]; then
            key="${key_map[$char]}"
        elif [[ "$char" =~ [A-Z] ]]; then
            key="shift-${char,,}"  # lowercase with shift
        else
            key="$char"
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

# wait for VM to boot by watching serial log for login prompt
wait_for_boot() {
    local max_attempts=90
    local attempt=0
    local serial_log="$WORK_DIR/serial.log"

    print_info "Waiting for VM to boot (checking SSH port)..."

    # first just wait for SSH port since Arch ISO starts sshd automatically
    while [[ $attempt -lt $max_attempts ]]; do
        if nc -z localhost "$SSH_PORT" 2>/dev/null; then
            print_success "SSH port is open"
            # wait additional time for the console to be fully ready
            # SSH port opens before the login shell is ready
            print_info "Waiting for console to be ready..."
            sleep 15
            return 0
        fi
        ((attempt++))
        sleep 2
    done

    print_warning "Timeout waiting for SSH port"
    return 1
}

# expand cowspace for package installations
expand_cowspace() {
    print_info "Expanding cowspace for package installations..."

    local ssh_opts="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
    if command -v sshpass &>/dev/null; then
        sshpass -p root ssh $ssh_opts -p "$SSH_PORT" root@localhost \
            "mount -o remount,size=2G /run/archiso/cowspace" 2>/dev/null && \
            print_success "Cowspace expanded to 2G" || \
            print_warning "Failed to expand cowspace - some packages may fail to install"
    fi
}

# set up root password via QEMU monitor sendkey
setup_root_password() {
    print_info "Setting up root password via console commands..."

    # wait longer for the login shell to be fully ready after boot
    # the ISO needs time to fully initialize
    sleep 25

    # set root password using chpasswd (same as Python implementation)
    send_console_command "echo root:root | chpasswd" 1

    print_success "Root password set to 'root'"
}

# verify SSH works
verify_ssh() {
    local max_attempts=5
    local attempt=0

    print_info "Verifying SSH connection..."

    # check if sshpass is available
    local use_sshpass=false
    if command -v sshpass &>/dev/null; then
        use_sshpass=true
    fi

    while [[ $attempt -lt $max_attempts ]]; do
        local ssh_result=0
        if [[ "$use_sshpass" == "true" ]]; then
            sshpass -p root ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                   -o ConnectTimeout=5 -o PasswordAuthentication=yes -o PubkeyAuthentication=no \
                   -p "$SSH_PORT" root@localhost "echo test" &>/dev/null || ssh_result=$?
        else
            ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                   -o ConnectTimeout=5 -o BatchMode=yes \
                   -p "$SSH_PORT" root@localhost "echo test" &>/dev/null || ssh_result=$?
        fi

        if [[ $ssh_result -eq 0 ]]; then
            print_success "SSH is working"
            return 0
        fi
        ((attempt++))
        sleep 2
    done

    print_warning "SSH verification failed - you may need to set root password manually"
    print_info "Try: sshpass -p root ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@localhost -p $SSH_PORT"
    return 1
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
    echo "Press Ctrl+C to stop the VM"
    echo ""
}

main() {
    print_info "QEMU Manual Test for Arch Installer"
    echo ""

    check_requirements
    check_iso
    setup_work_dir

    trap cleanup EXIT

    local qemu_cmd
    qemu_cmd=$(build_qemu_command)

    print_info "Starting QEMU..."
    echo "Command: $qemu_cmd"
    echo ""

    # run QEMU (daemonizes itself)
    eval "$qemu_cmd"

    # wait for serial socket to be ready
    sleep 2

    print_connection_info

    # wait for VM to boot and auto-setup SSH
    if wait_for_boot; then
        setup_root_password
        if verify_ssh; then
            expand_cowspace
        fi
    fi

    echo ""
    print_success "VM is running. Press Enter to stop and clean up, or Ctrl+C."
    echo ""

    # wait for user input
    read -r

    print_info "Shutting down..."
}

main "$@"
