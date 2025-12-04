"""QEMU virtual machine runner for installation testing.

provides QemuVm class for creating and managing QEMU VMs with UEFI
secure boot support for testing the arch installer.
"""

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SecureBootMode(Enum):
    """secure boot configuration mode for QEMU VM."""

    DISABLED = "disabled"
    SETUP_MODE = "setup"  # secure boot enabled but no keys enrolled
    ENROLLED = "enrolled"  # secure boot with keys enrolled


class QemuArchitecture(Enum):
    """target architecture for QEMU."""

    X86_64 = "x86_64"
    AARCH64 = "aarch64"


@dataclass(frozen=True)
class OvmfPaths:
    """paths to OVMF firmware files."""

    code: Path  # OVMF_CODE.fd - firmware code
    vars_template: Path  # OVMF_VARS.fd - variables template (read-only)


@dataclass(frozen=True)
class QemuConfig:
    """configuration for QEMU VM."""

    memory_mb: int = 4096
    cpus: int = 2
    disk_size_gb: int = 20
    architecture: QemuArchitecture = QemuArchitecture.X86_64
    secure_boot: SecureBootMode = SecureBootMode.SETUP_MODE
    enable_kvm: bool = True
    headless: bool = True
    ssh_port: int = 2222
    monitor_port: int = 4444
    serial_log: Optional[Path] = None


@dataclass
class QemuPaths:
    """runtime paths for a QEMU VM instance."""

    work_dir: Path
    disk_image: Path
    ovmf_vars: Path  # writable copy of OVMF_VARS
    serial_log: Path
    pid_file: Path
    monitor_socket: Path


class QemuError(Exception):
    """error during QEMU operations."""

    pass


class OvmfNotFoundError(QemuError):
    """OVMF firmware files not found."""

    pass


def find_ovmf_paths(architecture: QemuArchitecture = QemuArchitecture.X86_64) -> OvmfPaths:
    """find OVMF firmware paths on the system.

    searches common locations for OVMF firmware files on macOS (homebrew)
    and Linux distributions.
    """
    search_paths: list[tuple[Path, Path]] = []

    if architecture == QemuArchitecture.X86_64:
        # macOS homebrew locations - secure variant preferred
        search_paths.extend(
            [
                (
                    Path("/opt/homebrew/share/qemu/edk2-x86_64-secure-code.fd"),
                    Path("/opt/homebrew/share/qemu/edk2-i386-vars.fd"),
                ),
                (
                    Path("/usr/local/share/qemu/edk2-x86_64-secure-code.fd"),
                    Path("/usr/local/share/qemu/edk2-i386-vars.fd"),
                ),
                # linux locations - secure boot variants first (required for setup mode)
                (
                    Path("/usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.4m.fd"),
                    Path("/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"),
                ),
                (
                    Path("/usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.fd"),
                    Path("/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"),
                ),
                # linux locations - non-secboot variants (fallback, no setup mode)
                (
                    Path("/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd"),
                    Path("/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"),
                ),
                (
                    Path("/usr/share/edk2-ovmf/x64/OVMF_CODE.fd"),
                    Path("/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"),
                ),
                (
                    Path("/usr/share/OVMF/OVMF_CODE.fd"),
                    Path("/usr/share/OVMF/OVMF_VARS.fd"),
                ),
                (
                    Path("/usr/share/edk2/ovmf/OVMF_CODE.fd"),
                    Path("/usr/share/edk2/ovmf/OVMF_VARS.fd"),
                ),
                # macOS homebrew non-secure fallback
                (
                    Path("/opt/homebrew/share/qemu/edk2-x86_64-code.fd"),
                    Path("/opt/homebrew/share/qemu/edk2-i386-vars.fd"),
                ),
                (
                    Path("/usr/local/share/qemu/edk2-x86_64-code.fd"),
                    Path("/usr/local/share/qemu/edk2-i386-vars.fd"),
                ),
            ]
        )

    for code_path, vars_path in search_paths:
        if code_path.exists() and vars_path.exists():
            return OvmfPaths(code=code_path, vars_template=vars_path)

    raise OvmfNotFoundError(
        f"OVMF firmware not found for {architecture.value}. "
        "On macOS: brew install qemu (includes EDK2 firmware). "
        "On Linux: install edk2-ovmf or ovmf package."
    )


def find_qemu_binary(architecture: QemuArchitecture = QemuArchitecture.X86_64) -> Path:
    """find QEMU system emulator binary."""
    binary_name = f"qemu-system-{architecture.value}"
    qemu_path = shutil.which(binary_name)

    if qemu_path is None:
        raise QemuError(
            f"{binary_name} not found. Install QEMU: brew install qemu (macOS) "
            "or pacman -S qemu-full (Arch Linux)"
        )

    return Path(qemu_path)


@dataclass
class QemuVm:
    """manages a QEMU virtual machine for testing.

    this class handles the full lifecycle of a QEMU VM including:
    - creating and configuring virtual disks
    - setting up UEFI firmware with secure boot
    - starting/stopping the VM
    - executing commands via SSH
    - capturing serial output for debugging
    """

    config: QemuConfig
    ovmf: OvmfPaths = field(init=False)
    qemu_binary: Path = field(init=False)
    paths: Optional[QemuPaths] = field(default=None, init=False)
    _process: Optional[subprocess.Popen] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.ovmf = find_ovmf_paths(self.config.architecture)
        self.qemu_binary = find_qemu_binary(self.config.architecture)

    def setup(self, work_dir: Optional[Path] = None) -> QemuPaths:
        """set up VM resources including disk and OVMF vars copy."""
        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix="qemu-arch-test-"))

        work_dir.mkdir(parents=True, exist_ok=True)

        paths = QemuPaths(
            work_dir=work_dir,
            disk_image=work_dir / "disk.qcow2",
            ovmf_vars=work_dir / "OVMF_VARS.fd",
            serial_log=work_dir / "serial.log",
            pid_file=work_dir / "qemu.pid",
            monitor_socket=work_dir / "monitor.sock",
        )

        # create disk image
        self._create_disk_image(paths.disk_image)

        # copy OVMF vars (needs to be writable for UEFI variable storage)
        shutil.copy(self.ovmf.vars_template, paths.ovmf_vars)

        self.paths = paths
        return paths

    def _create_disk_image(self, path: Path) -> None:
        """create a qcow2 disk image."""
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                str(path),
                f"{self.config.disk_size_gb}G",
            ],
            check=True,
            capture_output=True,
        )

    def build_command(self, iso_path: Optional[Path] = None) -> list[str]:
        """build the QEMU command line arguments."""
        if self.paths is None:
            raise QemuError("VM not set up. Call setup() first.")

        cmd = [str(self.qemu_binary)]

        # machine and CPU - use hardware acceleration only if available
        use_hvf = self._use_hvf()
        use_accel = self._use_acceleration()

        if use_hvf:
            cmd.extend(["-machine", "q35,smm=on,accel=hvf"])
        elif use_accel:
            # linux with KVM
            cmd.extend(["-machine", "q35,smm=on"])
            cmd.extend(["-enable-kvm"])
        else:
            # software emulation (TCG) - needed for x86_64 on Apple Silicon
            cmd.extend(["-machine", "q35,smm=on,accel=tcg"])

        cmd.extend(["-cpu", "host" if use_accel else "qemu64"])
        cmd.extend(["-smp", str(self.config.cpus)])
        cmd.extend(["-m", str(self.config.memory_mb)])

        # UEFI firmware
        cmd.extend(
            [
                "-drive",
                f"if=pflash,format=raw,readonly=on,file={self.ovmf.code}",
            ]
        )
        cmd.extend(
            [
                "-drive",
                f"if=pflash,format=raw,file={self.paths.ovmf_vars}",
            ]
        )

        # enable SMM for secure boot
        if self.config.secure_boot != SecureBootMode.DISABLED:
            cmd.extend(["-global", "driver=cfi.pflash01,property=secure,value=on"])

        # disk
        cmd.extend(
            [
                "-drive",
                f"file={self.paths.disk_image},format=qcow2,if=virtio",
            ]
        )

        # ISO boot if provided
        if iso_path is not None:
            cmd.extend(
                [
                    "-cdrom",
                    str(iso_path),
                    "-boot",
                    "d",
                ]
            )

        # networking with SSH forwarding
        cmd.extend(
            [
                "-netdev",
                f"user,id=net0,hostfwd=tcp::{self.config.ssh_port}-:22",
                "-device",
                "virtio-net-pci,netdev=net0",
            ]
        )

        # serial console with telnet for bidirectional communication
        # this allows reading output and sending input for LUKS passphrase entry
        cmd.extend(
            [
                "-chardev",
                f"socket,id=serial0,path={self.paths.work_dir}/serial.sock,server=on,wait=off,logfile={self.paths.serial_log}",
                "-serial",
                "chardev:serial0",
            ]
        )

        # monitor
        cmd.extend(["-monitor", f"unix:{self.paths.monitor_socket},server,nowait"])

        # display - use VNC for headless mode so sendkey still works
        if self.config.headless:
            cmd.extend(["-display", "none"])
            cmd.extend(["-vnc", "127.0.0.1:99,to=199"])  # VNC port 5999-6099
        else:
            cmd.extend(["-display", "sdl"])

        # PID file
        cmd.extend(["-pidfile", str(self.paths.pid_file)])

        # daemon mode
        cmd.extend(["-daemonize"])

        return cmd

    def _use_hvf(self) -> bool:
        """check if we should use macOS Hypervisor.framework.

        HVF on Apple Silicon only works for aarch64, not x86_64 emulation.
        """
        import platform

        if platform.system() != "Darwin" or not self.config.enable_kvm:
            return False

        # on Apple Silicon (arm64), HVF only works for aarch64 guests
        machine = platform.machine()
        if machine == "arm64":
            return self.config.architecture == QemuArchitecture.AARCH64

        # on Intel Macs, HVF works for x86_64
        return self.config.architecture == QemuArchitecture.X86_64

    def _use_acceleration(self) -> bool:
        """check if hardware acceleration is available."""
        import platform

        system = platform.system()
        if system == "Darwin":
            # check if HVF is usable for this architecture
            return self._use_hvf()
        elif system == "Linux":
            # check for KVM
            return Path("/dev/kvm").exists()
        return False

    def start(self, iso_path: Optional[Path] = None) -> None:
        """start the QEMU VM."""
        if self.paths is None:
            raise QemuError("VM not set up. Call setup() first.")

        cmd = self.build_command(iso_path)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise QemuError(f"Failed to start QEMU: {result.stderr}")

        # wait for VM to start
        time.sleep(2)

        if not self.is_running():
            serial_output = self.get_serial_output()
            raise QemuError(f"VM failed to start. Serial output:\n{serial_output}")

    def stop(self, timeout: int = 30) -> None:
        """stop the QEMU VM gracefully."""
        if self.paths is None or not self.is_running():
            return

        # send poweroff via monitor
        try:
            self._send_monitor_command("system_powerdown")
            # wait for graceful shutdown
            for _ in range(timeout):
                if not self.is_running():
                    return
                time.sleep(1)
        except Exception:
            pass

        # force kill if still running
        self.kill()

    def kill(self) -> None:
        """forcefully terminate the QEMU VM."""
        if self.paths is None:
            return

        if self.paths.pid_file.exists():
            try:
                pid = int(self.paths.pid_file.read_text().strip())
                subprocess.run(["kill", "-9", str(pid)], capture_output=True)
            except (ValueError, subprocess.CalledProcessError):
                pass

    def is_running(self) -> bool:
        """check if the VM is running."""
        if self.paths is None or not self.paths.pid_file.exists():
            return False

        try:
            pid = int(self.paths.pid_file.read_text().strip())
            # check if process exists
            subprocess.run(["kill", "-0", str(pid)], check=True, capture_output=True)
            return True
        except (ValueError, subprocess.CalledProcessError):
            return False

    def _send_monitor_command(self, command: str) -> str:
        if self.paths is None:
            raise QemuError("VM not set up")

        import socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(self.paths.monitor_socket))
        sock.settimeout(5)

        try:
            # read initial prompt/banner
            try:
                sock.recv(4096)
            except socket.timeout:
                pass
            # send command
            sock.send(f"{command}\n".encode())
            time.sleep(0.1)
            # try to read response
            try:
                response = sock.recv(4096).decode()
            except socket.timeout:
                response = ""
            return response
        finally:
            sock.close()

    def send_console_command(self, command: str, wait_after: float = 0.5) -> None:
        """send a command to the VM via QEMU monitor's sendkey.

        this types the command character by character into the VM console,
        useful for initial setup before SSH is available.
        """
        if self.paths is None:
            raise QemuError("VM not set up")

        import socket

        # mapping of special characters to QEMU key names
        key_map = {
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

        # use a single socket connection for all keystrokes
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(self.paths.monitor_socket))
        sock.settimeout(5)

        try:
            # read initial prompt/banner
            try:
                sock.recv(4096)
            except socket.timeout:
                pass

            for char in command:
                if char in key_map:
                    key = key_map[char]
                elif char.isupper():
                    key = f"shift-{char.lower()}"
                else:
                    key = char
                sock.send(f"sendkey {key}\n".encode())
                time.sleep(0.05)  # delay between keystrokes

            # send enter
            sock.send("sendkey ret\n".encode())
        finally:
            sock.close()

        time.sleep(wait_after)

    def get_serial_output(self) -> str:
        """get the serial console output, sanitized for terminal display.

        filters out non-printable characters and ANSI escape sequences
        that could distort the terminal.
        """
        if self.paths is None or not self.paths.serial_log.exists():
            return ""
        try:
            import re

            raw_content = self.paths.serial_log.read_bytes()
            # decode with error handling for invalid UTF-8
            text = raw_content.decode("utf-8", errors="replace")
            # strip ANSI escape sequences (CSI sequences like [0m, [1;37m, etc.)
            ansi_pattern = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")
            text = ansi_pattern.sub("", text)
            # filter to printable chars plus whitespace (newline, tab, carriage return)
            sanitized = "".join(char for char in text if char.isprintable() or char in "\n\t\r")
            return sanitized
        except Exception:
            return "[serial output unreadable]"

    def wait_for_serial_prompt(
        self, prompt_pattern: str, timeout: int = 120, poll_interval: float = 2.0
    ) -> bool:
        """wait for a specific pattern to appear in serial output.

        args:
            prompt_pattern: string pattern to wait for (case-insensitive)
            timeout: maximum time to wait in seconds
            poll_interval: time between checks in seconds

        returns:
            True if pattern found, False if timeout
        """
        import sys

        start_time = time.time()
        prompt_lower = prompt_pattern.lower()
        check_count = 0

        while time.time() - start_time < timeout:
            output = self.get_serial_output()
            check_count += 1
            # debug every 10 checks
            if check_count % 10 == 1:
                print(
                    f">>>>> serial check #{check_count}, output len={len(output)}, "
                    f"looking for '{prompt_pattern}'",
                    file=sys.stderr,
                    flush=True,
                )
            if prompt_lower in output.lower():
                print(f">>>>> found pattern at check #{check_count}", file=sys.stderr, flush=True)
                return True
            time.sleep(poll_interval)

        print(
            f">>>>> pattern '{prompt_pattern}' NOT found after {check_count} checks",
            file=sys.stderr,
            flush=True,
        )
        return False

    def send_console_text(self, text: str, press_enter: bool = True) -> None:
        """send text to the VM serial console.

        this sends text directly to the serial console socket for responding
        to prompts like LUKS passphrase entry.

        args:
            text: the text to type
            press_enter: whether to press enter after typing the text
        """
        import sys

        if self.paths is None:
            raise QemuError("VM not set up")

        import socket

        serial_socket_path = self.paths.work_dir / "serial.sock"
        if not serial_socket_path.exists():
            raise QemuError(f"Serial socket not found: {serial_socket_path}")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)

        try:
            sock.connect(str(serial_socket_path))
            print(f">>>>> connected to serial socket", file=sys.stderr, flush=True)

            # small delay after connecting
            time.sleep(0.5)

            # send the text character by character with small delays
            for i, char in enumerate(text):
                sock.send(char.encode("utf-8"))
                time.sleep(0.02)  # 20ms delay between chars

            print(f">>>>> sent {len(text)} chars to serial", file=sys.stderr, flush=True)

            if press_enter:
                time.sleep(0.1)  # small delay before enter
                sock.send(b"\n")
                print(">>>>> sent LF", file=sys.stderr, flush=True)

            # wait a bit before closing to let data be processed
            time.sleep(1)
        finally:
            sock.close()

        # give more time for the input to be processed
        time.sleep(1)

    def wait_for_ssh(self, timeout: int = 300) -> bool:
        """wait for SSH to become available."""
        import socket

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(("127.0.0.1", self.config.ssh_port))
                sock.close()
                if result == 0:
                    return True
            except socket.error:
                pass
            time.sleep(2)
        return False

    def run_ssh_command(
        self,
        command: str,
        user: str = "root",
        password: str = "root",
        timeout: int = 60,
    ) -> tuple[int, str, str]:
        # use sshpass if available, otherwise try without password
        sshpass_path = shutil.which("sshpass")

        ssh_opts = [
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
            "-p",
            str(self.config.ssh_port),
        ]

        if sshpass_path:
            ssh_cmd = [
                sshpass_path,
                "-p",
                password,
                "ssh",
                *ssh_opts,
                f"{user}@127.0.0.1",
                command,
            ]
        else:
            # fallback: try SSH with keyboard-interactive
            # this works if the ISO accepts empty/no password
            ssh_cmd = [
                "ssh",
                *ssh_opts,
                "-o",
                "BatchMode=no",
                f"{user}@127.0.0.1",
                command,
            ]

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=password + "\n" if not sshpass_path else None,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "SSH command timed out"

    def copy_file_to_vm(
        self,
        local_path: Path,
        remote_path: str,
        user: str = "root",
        password: str = "root",
    ) -> None:
        sshpass_path = shutil.which("sshpass")

        scp_opts = [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "PasswordAuthentication=yes",
            "-o",
            "PubkeyAuthentication=no",
            "-P",
            str(self.config.ssh_port),
        ]

        if sshpass_path:
            scp_cmd = [
                sshpass_path,
                "-p",
                password,
                "scp",
                *scp_opts,
                str(local_path),
                f"{user}@127.0.0.1:{remote_path}",
            ]
        else:
            scp_cmd = [
                "scp",
                *scp_opts,
                str(local_path),
                f"{user}@127.0.0.1:{remote_path}",
            ]

        subprocess.run(scp_cmd, check=True, capture_output=True, input=password.encode() + b"\n")

    def copy_dir_to_vm(
        self,
        local_path: Path,
        remote_path: str,
        user: str = "root",
        password: str = "root",
    ) -> None:
        sshpass_path = shutil.which("sshpass")

        scp_opts = [
            "-r",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "PasswordAuthentication=yes",
            "-o",
            "PubkeyAuthentication=no",
            "-P",
            str(self.config.ssh_port),
        ]

        if sshpass_path:
            scp_cmd = [
                sshpass_path,
                "-p",
                password,
                "scp",
                *scp_opts,
                str(local_path),
                f"{user}@127.0.0.1:{remote_path}",
            ]
        else:
            scp_cmd = [
                "scp",
                *scp_opts,
                str(local_path),
                f"{user}@127.0.0.1:{remote_path}",
            ]

        subprocess.run(scp_cmd, check=True, capture_output=True, input=password.encode() + b"\n")

    def reboot(
        self,
        wait_for_ssh: bool = True,
        timeout: int = 300,
        user: str = "root",
        password: str = "root",
        luks_passphrase: Optional[str] = None,
    ) -> bool:
        """reboot the VM and optionally wait for SSH to come back.

        this restarts the VM by stopping and starting it again without
        the ISO attached, so it boots from the installed disk.

        args:
            wait_for_ssh: whether to wait for SSH after reboot
            timeout: timeout in seconds for SSH wait
            user: SSH user for post-reboot connection
            password: SSH password for post-reboot connection
            luks_passphrase: LUKS encryption passphrase to enter at boot prompt

        returns:
            True if SSH is available after reboot (or if wait_for_ssh=False)
        """
        if self.paths is None:
            raise QemuError("VM not set up. Call setup() first.")

        # stop the running VM
        self.stop(timeout=30)

        # small delay to ensure clean shutdown
        time.sleep(2)

        # start the VM again without ISO (boots from disk)
        cmd = self.build_command(iso_path=None)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise QemuError(f"Failed to restart QEMU: {result.stderr}")

        # wait for VM to start
        time.sleep(2)

        if not self.is_running():
            serial_output = self.get_serial_output()
            raise QemuError(f"VM failed to start after reboot. Serial output:\n{serial_output}")

        # if LUKS passphrase is provided, wait for prompt and enter it
        if luks_passphrase:
            import sys

            print(">>>>> LUKS handling started", file=sys.stderr, flush=True)
            print("    waiting for LUKS passphrase prompt...", flush=True)
            # wait for the LUKS passphrase prompt (give time for bootloader menu ~20s + boot)
            prompt_found = self.wait_for_serial_prompt("passphrase", timeout=120)
            print(f">>>>> prompt_found = {prompt_found}", file=sys.stderr, flush=True)
            if prompt_found:
                print("    LUKS prompt detected, entering passphrase...", flush=True)
                # small delay to ensure the prompt is ready for input
                time.sleep(2)
                self.send_console_text(luks_passphrase, press_enter=True)
                print("    passphrase sent, waiting for decryption...", flush=True)
                # give time for decryption to start
                time.sleep(10)
            else:
                print("    warning: LUKS passphrase prompt not detected", flush=True)
                # print the current serial output for debugging
                current_output = self.get_serial_output()
                print(f"    serial output tail: {current_output[-500:]}", flush=True)

        if not wait_for_ssh:
            return True

        # wait for SSH to become available
        print("    waiting for SSH after reboot...")
        if not self.wait_for_ssh(timeout=timeout):
            serial_output = self.get_serial_output()
            raise QemuError(f"SSH not available after reboot. Serial output:\n{serial_output}")

        # verify SSH actually works
        print("    verifying SSH connection...")
        max_attempts = 15
        last_stdout = ""
        last_stderr = ""
        for attempt in range(max_attempts):
            exit_code, stdout, stderr = self.run_ssh_command(
                "echo connected",
                user=user,
                password=password,
                timeout=30,
            )
            last_stdout = stdout
            last_stderr = stderr
            if exit_code == 0 and "connected" in stdout:
                print(f"    SSH working after reboot (attempt {attempt + 1})")
                return True
            print(f"    SSH attempt {attempt + 1}/{max_attempts}: exit={exit_code}")
            time.sleep(5)

        serial_output = self.get_serial_output()
        raise QemuError(
            f"SSH connection failed after reboot\n"
            f"Last stdout: {last_stdout}\n"
            f"Last stderr: {last_stderr}\n"
            f"Serial output (last 5000 chars):\n{serial_output[-5000:]}"
        )

    def cleanup(self) -> None:
        """stop VM and clean up all resources."""
        self.stop()

        # force kill again to ensure process is dead
        self.kill()

        # small delay to let the OS release ports
        time.sleep(1)

        if self.paths is not None:
            shutil.rmtree(self.paths.work_dir, ignore_errors=True)
            self.paths = None

    def __enter__(self) -> "QemuVm":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()


def setup_live_iso_network(vm: "QemuVm", timeout: int = 60) -> bool:
    """configure networking in the live arch ISO.

    the arch ISO requires manual network setup. this function
    starts dhcpcd on the virtio network interface.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            exit_code, stdout, stderr = vm.run_ssh_command(
                "dhcpcd -w ens3 2>/dev/null || dhcpcd -w eth0 2>/dev/null || true",
                timeout=30,
            )
            if exit_code == 0:
                return True
        except Exception:
            pass
        time.sleep(5)

    return False


def setup_ssh_in_live_iso(vm: "QemuVm") -> bool:
    """enable and start SSH in the live ISO environment.

    by default the arch ISO has sshd but it needs to be started
    and root password needs to be set for password auth.
    """
    commands = [
        "echo 'root:root' | chpasswd",
        "sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config",
        "sed -i 's/PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config",
        "systemctl start sshd",
    ]

    for cmd in commands:
        exit_code, _, _ = vm.run_ssh_command(cmd, timeout=30)
        if exit_code != 0:
            return False

    return True


def install_sbctl_in_live_iso(vm: QemuVm, cache_proxy_url: str | None = None) -> bool:
    """install sbctl in the live ISO for secure boot key enrollment."""
    if cache_proxy_url:
        mirrorlist_cmd = (
            f'echo "Server = {cache_proxy_url}/\\$repo/os/\\$arch" > /etc/pacman.d/mirrorlist'
        )
        exit_code, _, _ = vm.run_ssh_command(mirrorlist_cmd, timeout=30)
        if exit_code != 0:
            return False

    keyring_commands = [
        "pacman-key --init",
        "pacman-key --populate archlinux",
        "pacman-key --refresh-keys 2>/dev/null || true",
    ]

    for cmd in keyring_commands:
        exit_code, stdout, stderr = vm.run_ssh_command(cmd, timeout=120)

    commands = [
        "pacman -Sy --noconfirm",
        "pacman -S --noconfirm sbctl",
    ]

    for cmd in commands:
        exit_code, stdout, stderr = vm.run_ssh_command(cmd, timeout=300)
        if exit_code != 0:
            print(f"command failed: {cmd}")
            print(f"stdout: {stdout}")
            print(f"stderr: {stderr}")
            return False

    return True


def configure_pacman_cache_proxy(vm: "QemuVm", proxy_host: str, proxy_port: int) -> bool:
    """configure pacman in VM to use the cache proxy."""
    mirror_url = f"http://{proxy_host}:{proxy_port}"
    mirrorlist_content = f"Server = {mirror_url}/$repo/os/$arch"

    exit_code, _, _ = vm.run_ssh_command(
        f'echo "{mirrorlist_content}" > /etc/pacman.d/mirrorlist',
        timeout=30,
    )
    return exit_code == 0


def wait_for_vm_boot_and_network(vm: "QemuVm", timeout: int = 180) -> bool:
    """wait for VM to boot and have network connectivity.

    for arch linux live ISO, we:
    1. wait for SSH port to become reachable (ISO starts sshd)
    2. set root password via console (needed for SSH auth)
    3. verify SSH works and set up network if needed
    """
    start_time = time.time()

    print("    waiting for VM to boot (checking SSH port)...")
    if not vm.wait_for_ssh(timeout=timeout):
        print(f"    timeout waiting for SSH port. serial log:\n{vm.get_serial_output()[-2000:]}")
        return False

    print("    SSH port open, waiting for sshd to fully initialize...")

    for _ in range(30):
        try:
            exit_code, _, stderr = vm.run_ssh_command("echo test", timeout=10)
            if exit_code == 5 or "Permission denied" in stderr:
                print("    sshd is ready")
                break
            if exit_code == 0:
                print("    SSH already works!")
                return True
        except Exception:
            pass
        time.sleep(2)
    else:
        print("    sshd didn't become ready")

    print("    setting up root password via console...")
    print("    sending commands to set root password...")
    vm.send_console_command("", wait_after=2)
    vm.send_console_command("echo root:root | chpasswd", wait_after=3)

    print("    testing SSH...")

    for attempt in range(15):
        try:
            exit_code, stdout, stderr = vm.run_ssh_command("echo test", timeout=15)
            if exit_code == 0 and "test" in stdout:
                print("    SSH working")
                break
            print(f"    SSH attempt {attempt+1}: exit={exit_code}, stderr={stderr[:80]}")
        except Exception as e:
            print(f"    SSH attempt {attempt+1} failed: {e}")
        time.sleep(3)
    else:
        print("    SSH authentication failed after retries")
        return False

    exit_code, _, _ = vm.run_ssh_command("ping -c 1 -W 5 archlinux.org", timeout=15)
    if exit_code != 0:
        print("    setting up network...")
        vm.run_ssh_command("dhcpcd -w", timeout=60)

    return True


def get_secure_boot_status(vm: "QemuVm") -> dict:
    """get detailed secure boot status from the VM.

    returns a dict with setup_mode, secure_boot_enabled, and
    any enrolled keys information.
    """
    result = {
        "setup_mode": False,
        "secure_boot_enabled": False,
        "has_keys": False,
        "sbctl_available": False,
        "raw_output": "",
    }

    setup_var = "/sys/firmware/efi/efivars/SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    exit_code, stdout, _ = vm.run_ssh_command(
        f"[ -f {setup_var} ] && od -An -t u1 -j4 -N1 {setup_var} || echo -1",
        timeout=10,
    )
    try:
        setup_byte = int(stdout.strip())
        result["setup_mode"] = setup_byte == 1
        result["raw_output"] += f"SetupMode efivar: {setup_byte}\n"
    except ValueError:
        result["raw_output"] += f"SetupMode read failed: {stdout}\n"

    secureboot_var = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    exit_code, stdout, _ = vm.run_ssh_command(
        f"[ -f {secureboot_var} ] && od -An -t u1 -j4 -N1 {secureboot_var} || echo -1",
        timeout=10,
    )
    try:
        secureboot_byte = int(stdout.strip())
        result["secure_boot_enabled"] = secureboot_byte == 1
        result["raw_output"] += f"SecureBoot efivar: {secureboot_byte}\n"
    except ValueError:
        result["raw_output"] += f"SecureBoot read failed: {stdout}\n"

    exit_code, stdout, _ = vm.run_ssh_command("which sbctl", timeout=10)
    result["sbctl_available"] = exit_code == 0

    if result["sbctl_available"]:
        exit_code, stdout, _ = vm.run_ssh_command("sbctl status", timeout=30)
        result["raw_output"] += f"sbctl status:\n{stdout}"
        if "keys" in stdout.lower():
            result["has_keys"] = "created" in stdout.lower()

    return result
