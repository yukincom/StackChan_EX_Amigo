from __future__ import annotations

import json
import zipfile

import pytest

from tools.stackchan_lab.recovery import (
    SnapshotError,
    create_snapshot,
    restore_snapshot,
    verify_snapshot,
)


def test_snapshot_round_trip_verifies_hashes(tmp_path):
    source = tmp_path / "source"
    (source / "env").mkdir(parents=True)
    (source / "server" / "json").mkdir(parents=True)
    (source / "server" / "memory").mkdir(parents=True)
    (source / "env" / ".env").write_text("ADMIN_TOKEN=fixture-only\n", encoding="utf-8")
    (source / "server" / "json" / "member.json").write_text("{}\n", encoding="utf-8")
    (source / "server" / "memory" / "today.md").write_text("test memory\n", encoding="utf-8")

    snapshot = tmp_path / "backup.stackchan-snapshot.zip"
    manifest = create_snapshot(source, snapshot)
    assert len(manifest["entries"]) == 3
    assert verify_snapshot(snapshot)["source_name"] == "source"

    restored = tmp_path / "restored"
    restore_snapshot(snapshot, restored)
    assert (restored / "server" / "memory" / "today.md").read_text(encoding="utf-8") == "test memory\n"
    assert snapshot.stat().st_mode & 0o077 == 0


def test_snapshot_rejects_unlisted_payload(tmp_path):
    source = tmp_path / "source"
    (source / "env").mkdir(parents=True)
    (source / "env" / ".env").write_text("SAFE=fixture\n", encoding="utf-8")
    snapshot = tmp_path / "backup.zip"
    create_snapshot(source, snapshot)
    with zipfile.ZipFile(snapshot, "a") as archive:
        archive.writestr("data/unlisted.txt", "unexpected")
    with pytest.raises(SnapshotError, match="unlisted"):
        verify_snapshot(snapshot)


def test_snapshot_rejects_path_traversal_manifest(tmp_path):
    snapshot = tmp_path / "unsafe.zip"
    manifest = {
        "schema_version": 1,
        "created_at": "fixture",
        "source_name": "fixture",
        "entries": [{"path": "../escape", "size": 1, "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(snapshot, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("data/../escape", b"x")
    with pytest.raises(SnapshotError, match="unsafe snapshot path"):
        verify_snapshot(snapshot)


def test_snapshot_rejects_symlink_and_output_inside_source(tmp_path):
    source = tmp_path / "source"
    (source / "env").mkdir(parents=True)
    target = source / "env" / "target.txt"
    target.write_text("fixture", encoding="utf-8")
    symlink = source / "env" / "link.txt"
    try:
        symlink.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(SnapshotError, match="output must be outside"):
        create_snapshot(source, source / "backup.zip")
    with pytest.raises(SnapshotError, match="symlink"):
        create_snapshot(source, tmp_path / "backup.zip")
    with pytest.raises(SnapshotError, match="symlink"):
        create_snapshot(source, tmp_path / "direct-link.zip", include=["env/link.txt"])


def test_snapshot_rejects_symlinked_include_root_and_ancestor(tmp_path):
    source = tmp_path / "source"
    private = source / "private"
    private.mkdir(parents=True)
    (private / "secret.txt").write_text("fixture", encoding="utf-8")
    env_link = source / "env"
    try:
        env_link.symlink_to(private, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(SnapshotError, match="symlink"):
        create_snapshot(source, tmp_path / "root-link.zip", include=["env"])
    with pytest.raises(SnapshotError, match="symlink"):
        create_snapshot(source, tmp_path / "ancestor-link.zip", include=["env/secret.txt"])


def test_restore_rejects_nonempty_or_file_target(tmp_path):
    source = tmp_path / "source"
    (source / "env").mkdir(parents=True)
    (source / "env" / ".env").write_text("FIXTURE=1\n", encoding="utf-8")
    snapshot = tmp_path / "backup.zip"
    create_snapshot(source, snapshot)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(SnapshotError, match="must be empty"):
        restore_snapshot(snapshot, nonempty)

    file_target = tmp_path / "file-target"
    file_target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(SnapshotError, match="must be a directory"):
        restore_snapshot(snapshot, file_target)
