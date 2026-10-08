"""QEMU virtual machines for the tests: disks, firmware, console, SSH and reboots."""

import contextlib
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

ANSI_ESCAPE_SEQUENCE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")

# QEMU monitor `sendkey` names for characters that are not plain lowercase/digits
SENDKEY_NAMES = {
    " ": "spc",
    "\n": "ret",
    "-": "minus",
    "=": "equal",
    "[": "bracket_left",
    "]": "bracket_right",
    ";": "semicolon",
    "'": "apostrophe",
    "\\": "backslash",
    ",": "comma",
    ".": "dot",
    "/": "slash",
    "`": "grave_accent",
    "!": "shift-1",
    "@": "shift-2",
    "#": "shift-3",
    "$": "shift-4",
    "%": "shift-5",
    "^": "shift-6",
    "&": "shift-7",
    "*": "shift-8",
    "(": "shift-9",
    ")": "shift-0",
    "_": "shift-minus",
    "+": "shift-equal",
    "{": "shift-bracket_left",
    "}": "shift-bracket_right",
    ":": "shift-semicolon",
    '"': "shift-apostrophe",
    "|": "shift-backslash",
    "<": "shift-comma",
    ">": "shift-dot",
    "?": "shift-slash",
    "~": "shift-grave_accent",
}


class SecureBootMode(Enum):
    DISABLED = "disabled"
    SETUP_MODE = "setup"  # secure boot enabled but no keys enrolled
    ENROLLED = "enrolled"  # secure boot with keys enrolled


class QemuArchitecture(Enum):
    X86_64 = "x86_64"
    AARCH64 = "aarch64"


@dataclass(frozen=True)
class OvmfPaths:
    code: Path  # OVMF_CODE.fd - firmware code
    vars_template: Path  # OVMF_VARS.fd - variables template (read-only)


@dataclass(frozen=True)
class QemuConfig:
    memory_mb: int = 4096
    cpus: int = 2
    disk_size_gb: int = 20
    extra_disks_gb: tuple[int, ...] = ()
    # USB mass storage on an xHCI controller: /dev/sda, /dev/sdb... in the guest
    usb_disks_gb: tuple[int, ...] = ()
    architecture: QemuArchitecture = QemuArchitecture.X86_64
    secure_boot: SecureBootMode = SecureBootMode.SETUP_MODE
    enable_kvm: bool = True
    headless: bool = True
    ssh_port: int = 2222


@dataclass
class QemuPaths:
    working_directory: Path
    disk_image: Path
    extra_disk_images: list[Path]
    usb_disk_images: list[Path]
    # the USB disks plugged in at the next start: a test unplugs one by leaving it out
    attached_usb_disk_images: list[Path]
    ovmf_vars: Path  # writable copy of OVMF_VARS
    serial_log: Path
    pid_file: Path
    monitor_socket: Path


class QemuError(Exception):
    pass


class OvmfNotFoundError(QemuError):
    pass


def find_ovmf_paths(architecture: QemuArchitecture = QemuArchitecture.X86_64) -> OvmfPaths:
    # secure boot capable firmware first: only that one starts in setup mode
    search_paths: list[tuple[str, str]] = []
    if architecture == QemuArchitecture.X86_64:
        search_paths = [
            (
                "/opt/homebrew/share/qemu/edk2-x86_64-secure-code.fd",
                "/opt/homebrew/share/qemu/edk2-i386-vars.fd",
            ),
            (
                "/usr/local/share/qemu/edk2-x86_64-secure-code.fd",
                "/usr/local/share/qemu/edk2-i386-vars.fd",
            ),
            (
                "/usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.4m.fd",
                "/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd",
            ),
            (
                "/usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.fd",
                "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd",
            ),
            (
                "/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd",
                "/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd",
            ),
            ("/usr/share/edk2-ovmf/x64/OVMF_CODE.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"),
            ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
            ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
            (
                "/opt/homebrew/share/qemu/edk2-x86_64-code.fd",
                "/opt/homebrew/share/qemu/edk2-i386-vars.fd",
            ),
            (
                "/usr/local/share/qemu/edk2-x86_64-code.fd",
                "/usr/local/share/qemu/edk2-i386-vars.fd",
            ),
        ]

    for code_path, vars_path in search_paths:
        if Path(code_path).exists() and Path(vars_path).exists():
            return OvmfPaths(code=Path(code_path), vars_template=Path(vars_path))

    raise OvmfNotFoundError(
        f"OVMF firmware not found for {architecture.value}. "
        "On macOS: brew install qemu (includes EDK2 firmware). "
        "On Linux: install edk2-ovmf or ovmf package."
    )


def find_qemu_binary(architecture: QemuArchitecture = QemuArchitecture.X86_64) -> Path:
    binary_name = f"qemu-system-{architecture.value}"
    qemu_path = shutil.which(binary_name)
    if qemu_path is None:
        raise QemuError(
            f"{binary_name} not found. Install QEMU: brew install qemu (macOS) "
            "or pacman -S qemu-full (Arch Linux)"
        )
    return Path(qemu_path)


def find_sshpass_binary() -> Path:
    # ssh only reads passwords from a terminal, so password auth needs sshpass
    sshpass_path = shutil.which("sshpass")
    if sshpass_path is None:
        raise QemuError("sshpass not found. Install it: pacman -S sshpass / brew install sshpass")
    return Path(sshpass_path)


@dataclass
class QemuVm:
    config: QemuConfig
    ovmf: OvmfPaths = field(init=False)
    qemu_binary: Path = field(init=False)
    sshpass_binary: Path = field(init=False)
    paths: QemuPaths | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.ovmf = find_ovmf_paths(self.config.architecture)
        self.qemu_binary = find_qemu_binary(self.config.architecture)
        self.sshpass_binary = find_sshpass_binary()

    def setup(self, working_directory: Path | None = None) -> QemuPaths:
        if working_directory is None:
            working_directory = Path(tempfile.mkdtemp(prefix="qemu-arch-test-"))
        working_directory.mkdir(parents=True, exist_ok=True)

        paths = QemuPaths(
            working_directory=working_directory,
            disk_image=working_directory / "disk.qcow2",
            extra_disk_images=[],
            usb_disk_images=[],
            attached_usb_disk_images=[],
            ovmf_vars=working_directory / "OVMF_VARS.fd",
            serial_log=working_directory / "serial.log",
            pid_file=working_directory / "qemu.pid",
            monitor_socket=working_directory / "monitor.sock",
        )

        self._create_disk_image(paths.disk_image, self.config.disk_size_gb)
        for index, size_gb in enumerate(self.config.extra_disks_gb):
            extra_path = working_directory / f"disk-extra-{index}.qcow2"
            self._create_disk_image(extra_path, size_gb)
            paths.extra_disk_images.append(extra_path)
        for index, size_gb in enumerate(self.config.usb_disks_gb):
            usb_path = working_directory / f"usb-disk-{index}.qcow2"
            self._create_disk_image(usb_path, size_gb)
            paths.usb_disk_images.append(usb_path)
        paths.attached_usb_disk_images = list(paths.usb_disk_images)

        # UEFI variables (enrolled keys) live in this file, so it must be a writable copy
        shutil.copy(self.ovmf.vars_template, paths.ovmf_vars)

        self.paths = paths
        return paths

    def _require_paths(self) -> QemuPaths:
        if self.paths is None:
            raise QemuError("VM not set up. Call setup() first.")
        return self.paths

    @staticmethod
    def _create_disk_image(path: Path, size_gb: int) -> None:
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(path), f"{size_gb}G"],
            check=True,
            capture_output=True,
        )

    def build_command(self, iso_path: Path | None = None) -> list[str]:
        paths = self._require_paths()
        use_acceleration = self._use_acceleration()

        command = [str(self.qemu_binary)]
        if self._use_hvf():
            command += ["-machine", "q35,smm=on,accel=hvf"]
        elif use_acceleration:
            command += ["-machine", "q35,smm=on", "-enable-kvm"]
        else:
            # software emulation (TCG), e.g. x86_64 guests on Apple Silicon
            command += ["-machine", "q35,smm=on,accel=tcg"]

        command += ["-cpu", "host" if use_acceleration else "qemu64"]
        command += ["-smp", str(self.config.cpus), "-m", str(self.config.memory_mb)]
        command += ["-drive", f"if=pflash,format=raw,readonly=on,file={self.ovmf.code}"]
        command += ["-drive", f"if=pflash,format=raw,file={paths.ovmf_vars}"]
        if self.config.secure_boot != SecureBootMode.DISABLED:
            command += ["-global", "driver=cfi.pflash01,property=secure,value=on"]

        for index, disk_image in enumerate([paths.disk_image, *paths.extra_disk_images]):
            # a serial number gives the disk a /dev/disk/by-id name, as real disks have
            drive_id = f"disk-{index}"
            command += ["-drive", f"if=none,id={drive_id},file={disk_image},format=qcow2"]
            command += ["-device", f"virtio-blk-pci,drive={drive_id},serial=dali-disk-{index}"]

        if paths.attached_usb_disk_images:
            command += ["-device", "qemu-xhci,id=xhci"]
        for index, usb_image in enumerate(paths.attached_usb_disk_images):
            drive_id = f"usb-disk-{index}"
            # without the live ISO the firmware starts from the USB drive, as from a stick
            boot_order = f",bootindex={index + 1}" if iso_path is None else ""
            command += ["-drive", f"if=none,id={drive_id},file={usb_image},format=qcow2"]
            command += [
                "-device",
                f"usb-storage,bus=xhci.0,drive={drive_id},removable=on{boot_order}",
            ]

        if iso_path is not None:
            command += ["-cdrom", str(iso_path), "-boot", "d"]

        command += [
            "-netdev",
            f"user,id=net0,hostfwd=tcp::{self.config.ssh_port}-:22",
            "-device",
            "virtio-net-pci,netdev=net0",
        ]
        # serial console on a socket: logged to a file and writable for the LUKS prompt
        command += [
            "-chardev",
            f"socket,id=serial0,path={paths.working_directory}/serial.sock,server=on,wait=off,"
            f"logfile={paths.serial_log}",
            "-serial",
            "chardev:serial0",
        ]
        command += ["-monitor", f"unix:{paths.monitor_socket},server,nowait"]
        if self.config.headless:
            # VNC instead of no display at all, so monitor sendkey still reaches a console
            command += ["-display", "none", "-vnc", "127.0.0.1:99,to=199"]
        else:
            command += ["-display", "sdl"]
        command += ["-pidfile", str(paths.pid_file), "-daemonize"]
        return command

    def _use_hvf(self) -> bool:
        # Hypervisor.framework only accelerates guests of the host's own architecture
        if platform.system() != "Darwin" or not self.config.enable_kvm:
            return False
        if platform.machine() == "arm64":
            return self.config.architecture == QemuArchitecture.AARCH64
        return self.config.architecture == QemuArchitecture.X86_64

    def _use_acceleration(self) -> bool:
        system = platform.system()
        if system == "Darwin":
            return self._use_hvf()
        if system == "Linux":
            return Path("/dev/kvm").exists()
        return False

    def start(self, iso_path: Path | None = None) -> None:
        result = subprocess.run(self.build_command(iso_path), capture_output=True, text=True)
        if result.returncode != 0:
            raise QemuError(f"Failed to start QEMU: {result.stderr}")

        time.sleep(2)
        if not self.is_running():
            raise QemuError(f"VM failed to start. Serial output:\n{self.get_serial_output()}")

    def stop(self, timeout: int = 30) -> None:
        if not self.is_running():
            return
        try:
            self._send_monitor_command("system_powerdown")
            for _ in range(timeout):
                if not self.is_running():
                    return
                time.sleep(1)
        except OSError:
            pass
        self.kill()

    def kill(self) -> None:
        pid = self._read_pid()
        if pid is not None:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)

    def is_running(self) -> bool:
        pid = self._read_pid()
        if pid is None:
            return False
        return subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode == 0

    def _read_pid(self) -> int | None:
        if self.paths is None or not self.paths.pid_file.exists():
            return None
        try:
            return int(self.paths.pid_file.read_text().strip())
        except ValueError:
            return None

    def _connect_monitor(self) -> socket.socket:
        monitor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        monitor.connect(str(self._require_paths().monitor_socket))
        monitor.settimeout(5)
        with contextlib.suppress(TimeoutError):
            monitor.recv(4096)  # banner and prompt
        return monitor

    def _send_monitor_command(self, command: str) -> str:
        with self._connect_monitor() as monitor:
            monitor.send(f"{command}\n".encode())
            time.sleep(0.1)
            try:
                return monitor.recv(4096).decode()
            except TimeoutError:
                return ""

    def send_console_command(self, command: str, wait_after: float = 0.5) -> None:
        # types the command on the VGA console with QEMU sendkey, which works before
        # SSH is reachable (e.g. to set the live ISO root password)
        with self._connect_monitor() as monitor:
            for char in command:
                if char in SENDKEY_NAMES:
                    key = SENDKEY_NAMES[char]
                elif char.isupper():
                    key = f"shift-{char.lower()}"
                else:
                    key = char
                monitor.send(f"sendkey {key}\n".encode())
                time.sleep(0.05)
            monitor.send(b"sendkey ret\n")
        time.sleep(wait_after)

    def get_serial_output(self) -> str:
        # sanitized so escape sequences can't garble the terminal showing test output
        if self.paths is None or not self.paths.serial_log.exists():
            return ""
        text = self.paths.serial_log.read_bytes().decode("utf-8", errors="replace")
        text = ANSI_ESCAPE_SEQUENCE.sub("", text)
        return "".join(char for char in text if char.isprintable() or char in "\n\t\r")

    def wait_for_serial_prompt(
        self, prompt_pattern: str, timeout: int = 120, poll_interval: float = 2.0
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if prompt_pattern.lower() in self.get_serial_output().lower():
                return True
            time.sleep(poll_interval)
        print(f">>>>> pattern '{prompt_pattern}' not found on serial", file=sys.stderr)
        return False

    def send_console_text(self, text: str, press_enter: bool = True) -> None:
        serial_socket_path = self._require_paths().working_directory / "serial.sock"
        if not serial_socket_path.exists():
            raise QemuError(f"Serial socket not found: {serial_socket_path}")

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as serial:
            serial.settimeout(10)
            serial.connect(str(serial_socket_path))
            time.sleep(0.5)
            # one character at a time, a fast burst gets dropped by the guest's tty
            for char in text:
                serial.send(char.encode("utf-8"))
                time.sleep(0.02)
            if press_enter:
                time.sleep(0.1)
                serial.send(b"\n")
            time.sleep(1)
        time.sleep(1)

    def wait_for_ssh(self, timeout: int = 300) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", self.config.ssh_port)) == 0:
                    return True
            time.sleep(2)
        return False

    def _ssh_options(self, port_flag: str) -> list[str]:
        return [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "PasswordAuthentication=yes",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "PreferredAuthentications=password,keyboard-interactive",
            port_flag,
            str(self.config.ssh_port),
        ]

    def run_ssh_command(
        self,
        command: str,
        user: str = "root",
        password: str = "root",
        timeout: int = 60,
    ) -> tuple[int, str, str]:
        ssh_command = [
            str(self.sshpass_binary),
            "-p",
            password,
            "ssh",
            *self._ssh_options("-p"),
            f"{user}@127.0.0.1",
            command,
        ]
        try:
            result = subprocess.run(ssh_command, capture_output=True, text=True, timeout=timeout)
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "SSH command timed out"

    def _scp(self, local_path: Path, remote_path: str, user: str, password: str) -> None:
        scp_command = [
            str(self.sshpass_binary),
            "-p",
            password,
            "scp",
            "-r",
            *self._ssh_options("-P"),
            str(local_path),
            f"{user}@127.0.0.1:{remote_path}",
        ]
        subprocess.run(scp_command, check=True, capture_output=True)

    def copy_file_to_vm(
        self, local_path: Path, remote_path: str, user: str = "root", password: str = "root"
    ) -> None:
        self._scp(local_path, remote_path, user, password)

    def copy_directory_to_vm(
        self, local_path: Path, remote_path: str, user: str = "root", password: str = "root"
    ) -> None:
        self._scp(local_path, remote_path, user, password)

    def reboot(
        self,
        wait_for_ssh: bool = True,
        timeout: int = 300,
        user: str = "root",
        password: str = "root",
        luks_passphrase: str | None = None,
    ) -> bool:
        # restarts QEMU without the ISO, so the firmware boots the installed disk
        self.stop(timeout=30)
        time.sleep(2)
        self.start(iso_path=None)

        if luks_passphrase:
            print("    waiting for LUKS passphrase prompt...", flush=True)
            # bootloader menu timeout plus kernel and initramfs start
            if self.wait_for_serial_prompt("passphrase", timeout=120):
                time.sleep(2)
                self.send_console_text(luks_passphrase, press_enter=True)
                print("    passphrase sent, waiting for decryption...", flush=True)
                time.sleep(10)
            else:
                print("    warning: LUKS passphrase prompt not detected", flush=True)
                print(f"    serial output tail: {self.get_serial_output()[-500:]}", flush=True)

        if not wait_for_ssh:
            return True

        print("    waiting for SSH after reboot...")
        if not self.wait_for_ssh(timeout=timeout):
            raise QemuError(f"SSH not available after reboot. Serial:\n{self.get_serial_output()}")

        last_stdout, last_stderr = "", ""
        for attempt in range(15):
            exit_code, last_stdout, last_stderr = self.run_ssh_command(
                "echo connected", user=user, password=password, timeout=30
            )
            if exit_code == 0 and "connected" in last_stdout:
                print(f"    SSH working after reboot (attempt {attempt + 1})")
                return True
            print(f"    SSH attempt {attempt + 1}/15: exit={exit_code}")
            time.sleep(5)

        raise QemuError(
            f"SSH connection failed after reboot\n"
            f"Last stdout: {last_stdout}\n"
            f"Last stderr: {last_stderr}\n"
            f"Serial output (last 5000 chars):\n{self.get_serial_output()[-5000:]}"
        )

    def cleanup(self) -> None:
        self.stop()
        self.kill()
        time.sleep(1)  # let the OS release the forwarded ports
        if self.paths is not None:
            shutil.rmtree(self.paths.working_directory, ignore_errors=True)
            self.paths = None

    def __enter__(self) -> "QemuVm":
        return self

    def __exit__(self, exception_type, exception, exception_traceback) -> None:
        self.cleanup()


def wait_for_vm_boot_and_network(vm: QemuVm, timeout: int = 180) -> bool:
    # the live ISO starts sshd but root has no password: wait for sshd to answer,
    # set the password on the autologin console, then confirm SSH works
    print("    waiting for VM to boot (checking SSH port)...")
    if not vm.wait_for_ssh(timeout=timeout):
        print(f"    timeout waiting for SSH port. serial log:\n{vm.get_serial_output()[-2000:]}")
        return False

    print("    SSH port open, waiting for sshd to fully initialize...")
    for _ in range(30):
        exit_code, _, stderr = vm.run_ssh_command("echo test", timeout=10)
        if exit_code == 0:
            print("    SSH already works!")
            return True
        # sshpass exits with 5 on a rejected password: sshd is up and asking
        if exit_code == 5 or "Permission denied" in stderr:
            print("    sshd is ready")
            break
        time.sleep(2)
    else:
        print("    sshd didn't become ready")

    print("    setting up root password via console...")
    vm.send_console_command("", wait_after=2)
    vm.send_console_command("echo root:root | chpasswd", wait_after=3)

    for attempt in range(15):
        exit_code, stdout, stderr = vm.run_ssh_command("echo test", timeout=15)
        if exit_code == 0 and "test" in stdout:
            print("    SSH working")
            break
        print(f"    SSH attempt {attempt + 1}: exit={exit_code}, stderr={stderr[:80]}")
        time.sleep(3)
    else:
        print("    SSH authentication failed after retries")
        return False

    exit_code, _, _ = vm.run_ssh_command("ping -c 1 -W 5 archlinux.org", timeout=15)
    if exit_code != 0:
        print("    setting up network...")
        vm.run_ssh_command("dhcpcd -w", timeout=60)

    return True
