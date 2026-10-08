"""Facts about the machine, such as its disks, offered as choices."""

from arch_installer.core.command import CommandRunner
from arch_installer.install_steps.questions import Disk


class SystemMachineFacts:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def disks(self) -> tuple[Disk, ...]:
        result = self._runner.run(["lsblk", "-dno", "NAME,TYPE"], raise_on_nonzero_exit=False)
        if not result.success:
            return ()
        disks = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "disk":
                path = f"/dev/{parts[0]}"
                model, size = self._model_and_size(path)
                disks.append(Disk(path, model, size))
        return tuple(disks)

    def _model_and_size(self, path: str) -> tuple[str, str]:
        result = self._runner.run(
            ["lsblk", "-dno", "MODEL,SIZE", path], raise_on_nonzero_exit=False
        )
        if not result.success:
            return "unknown", "unknown"
        # the model may contain spaces, the size never does
        parts = result.stdout.strip().rsplit(maxsplit=1)
        if len(parts) < 2:
            return "unknown", parts[0] if parts else "unknown"
        return parts[0], parts[1]
