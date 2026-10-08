"""drive a curses program running in a tmux session on the VM.

tmux renders the program into a real terminal, so a screen is matched against the
pane as it is displayed rather than against the stream of partial curses redraws.
"""

import shlex
import time
from dataclasses import dataclass

from tests.qemu.vm import QemuVm

TMUX_KEY_NAMES = {"Enter", "Up", "Down", "Space", "Tab", "y"}
KEY_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True)
class TmuxScreenInput:
    screen_text: str
    keys: tuple[str, ...]  # tmux key names; anything else is typed literally


class TmuxSession:
    def __init__(self, vm: QemuVm, name: str) -> None:
        self._vm = vm
        self._name = name
        self._exit_code_path = f"/tmp/{name}_exit_code"
        self.stderr_path = f"/tmp/{name}_stderr.log"

    def start(self, command: str, columns: int = 120, rows: int = 40) -> None:
        wrapped = f'{command} 2>{self.stderr_path}; echo "$?" > {self._exit_code_path}'
        exit_code, _, stderr = self._vm.run_ssh_command(
            f"tmux new-session -d -s {self._name} -x {columns} -y {rows} {shlex.quote(wrapped)}",
            timeout=30,
        )
        assert exit_code == 0, f"failed to start tmux session {self._name}: {stderr}"

    def pane(self) -> str:
        _, pane, _ = self._vm.run_ssh_command(
            f"tmux capture-pane -t {self._name} -p 2>/dev/null", timeout=10
        )
        return pane

    def drive(self, screen: TmuxScreenInput, timeout: int = 30) -> None:
        print(f"    {screen.screen_text}: {' '.join(screen.keys)}")
        deadline = time.time() + timeout
        while screen.screen_text not in self.pane():
            if time.time() > deadline:
                raise AssertionError(
                    f"TUI screen did not show '{screen.screen_text}' within {timeout}s:\n"
                    f"{self.pane()}"
                )
            time.sleep(KEY_INTERVAL_SECONDS)

        for key in screen.keys:
            literal_flag = "" if key in TMUX_KEY_NAMES else "-l "
            self._vm.run_ssh_command(
                f"tmux send-keys -t {self._name} {literal_flag}{shlex.quote(key)}", timeout=10
            )
            time.sleep(KEY_INTERVAL_SECONDS)

    def wait_for_exit(self, timeout: int, poll_seconds: int = 10) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            exit_code, result, _ = self._vm.run_ssh_command(
                f"cat {self._exit_code_path} 2>/dev/null", timeout=10
            )
            if exit_code == 0 and result.strip().isdigit():
                return int(result.strip())
            time.sleep(poll_seconds)

        _, stderr_log, _ = self._vm.run_ssh_command(
            f"cat {self.stderr_path} 2>/dev/null", timeout=10
        )
        raise AssertionError(
            f"tmux session {self._name} did not exit within {timeout}s\n"
            f"stderr: {stderr_log}\npane: {self.pane()}"
        )
