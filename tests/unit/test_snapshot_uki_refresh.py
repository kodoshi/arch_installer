import os
import subprocess
from pathlib import Path

import pytest

SNAPSHOT_MANAGER_SCRIPT = Path(__file__).parents[2] / "scripts" / "manage_snapshot_entries.sh"

# refresh_entries builds and signs UKIs on a real ESP; this fake records which snapshots each
# refresh saw, and takes the snapshots that snap-pac (or a timer) would take meanwhile
FAKE_REFRESH = """
refresh_count=0
refresh_entries() {
    refresh_count=$((refresh_count + 1))
    ls "$SNAPSHOTS_DIR" | sort -n | paste -sd ' ' >> "$REFRESH_LOG"
    local taken_during_refreshes=(${SNAPSHOTS_TAKEN_DURING_REFRESHES:-})
    local number="${taken_during_refreshes[refresh_count - 1]:-}"
    if [ -n "$number" ]; then
        mkdir -p "$SNAPSHOTS_DIR/$number/snapshot"
        touch "$SNAPSHOTS_DIR/$number/info.xml"
    fi
}
"""


def take_snapshot(snapshots_directory: Path, number: int) -> None:
    (snapshots_directory / str(number) / "snapshot").mkdir(parents=True)
    (snapshots_directory / str(number) / "info.xml").write_text(f"<num>{number}</num>")


@pytest.fixture
def snapshots_directory(tmp_path) -> Path:
    directory = tmp_path / "snapshots"
    directory.mkdir()
    take_snapshot(directory, 1)
    return directory


def refresh_until_settled(
    snapshots_directory: Path, taken_during_refreshes: list[int]
) -> tuple[list[str], str]:
    refresh_log = snapshots_directory.parent / "refreshes.log"
    script = (
        f"source {SNAPSHOT_MANAGER_SCRIPT}\n{FAKE_REFRESH}\n"
        f"SNAPSHOTS_DIR={snapshots_directory}; SNAPSHOTS_SETTLE_SECONDS=0; MAX_REFRESH_ROUNDS=3; "
        "refresh_entries_until_settled 3"
    )
    completed = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "REFRESH_LOG": str(refresh_log),
            "SNAPSHOTS_TAKEN_DURING_REFRESHES": " ".join(map(str, taken_during_refreshes)),
        },
    )
    return refresh_log.read_text().splitlines(), completed.stdout


class TestRefreshUntilSnapshotsSettle:
    def test_unchanged_snapshots_are_refreshed_once(self, snapshots_directory):
        snapshots_seen, _ = refresh_until_settled(snapshots_directory, [])

        assert snapshots_seen == ["1"]

    def test_snapshot_taken_during_a_refresh_gets_another_refresh(self, snapshots_directory):
        snapshots_seen, _ = refresh_until_settled(snapshots_directory, [2])

        assert snapshots_seen == ["1", "1 2"]

    def test_stops_when_snapshots_keep_changing(self, snapshots_directory):
        snapshots_seen, output = refresh_until_settled(snapshots_directory, [2, 3, 4, 5])

        assert snapshots_seen == ["1", "1 2", "1 2 3"]
        assert "kept changing" in output
