"""Ingestion tests — adversarial archives first, happy paths second."""

import io
import stat
import zipfile
from pathlib import Path

import pytest

from legacylens.core.config import Settings
from legacylens.core.exceptions import (
    ArchiveBombError,
    ArchiveTooLargeError,
    IngestionError,
    UnsafeArchiveError,
)
from legacylens.ingestion.extractor import extract_zip
from legacylens.ingestion.ingestor import ProjectIngestor
from legacylens.ingestion.inventory import build_inventory
from legacylens.ingestion.languages import detect_language
from legacylens.ingestion.safety import inspect_zip


def settings(**overrides) -> Settings:
    base = dict(
        max_archive_size_mb=100,
        max_extracted_size_mb=100,
        max_file_count=1000,
        bomb_compression_ratio_limit=150,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def write_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


class TestArchiveSafety:
    def test_path_traversal_rejected(self, tmp_path):
        archive = write_zip(tmp_path / "evil.zip", {"../../evil.sh": b"rm -rf /"})
        with pytest.raises(UnsafeArchiveError, match="traversal"):
            inspect_zip(archive, settings())

    def test_absolute_path_rejected(self, tmp_path):
        archive = write_zip(tmp_path / "evil.zip", {"/etc/cron.d/evil": b"x"})
        with pytest.raises(UnsafeArchiveError, match="Absolute"):
            inspect_zip(archive, settings())

    def test_windows_drive_path_rejected(self, tmp_path):
        archive = write_zip(
            tmp_path / "evil.zip", {"C:\\Windows\\system32\\evil.dll": b"x"}
        )
        with pytest.raises(UnsafeArchiveError, match="Absolute"):
            inspect_zip(archive, settings())

    def test_symlink_member_rejected(self, tmp_path):
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
        with pytest.raises(UnsafeArchiveError, match="Symlink"):
            inspect_zip(archive, settings())

    def test_compression_bomb_rejected(self, tmp_path):
        # 12 MiB of zeros compresses to ~12 KiB: ratio ~1000:1.
        archive = write_zip(
            tmp_path / "bomb.zip", {"zeros.bin": b"\x00" * (12 * 1024 * 1024)}
        )
        with pytest.raises(ArchiveBombError, match="ratio"):
            inspect_zip(archive, settings())

    def test_declared_size_ceiling_enforced(self, tmp_path):
        archive = write_zip(
            tmp_path / "big.zip", {"a.txt": b"hello world " * 200_000}
        )
        with pytest.raises(ArchiveTooLargeError, match="extracted bytes"):
            inspect_zip(archive, settings(max_extracted_size_mb=1))

    def test_file_count_ceiling_enforced(self, tmp_path):
        members = {f"f{i}.txt": b"x" for i in range(20)}
        archive = write_zip(tmp_path / "many.zip", members)
        with pytest.raises(ArchiveTooLargeError, match="files"):
            inspect_zip(archive, settings(max_file_count=10))

    def test_not_a_zip_rejected(self, tmp_path):
        fake = tmp_path / "fake.zip"
        fake.write_bytes(b"this is not a zip at all")
        with pytest.raises(IngestionError, match="Not a valid zip"):
            inspect_zip(fake, settings())

    def test_clean_archive_passes(self, tmp_path):
        archive = write_zip(
            tmp_path / "ok.zip",
            {"src/app.py": b"print('hi')", "README.md": b"# App"},
        )
        stats = inspect_zip(archive, settings())
        assert stats.file_count == 2


class TestExtractor:
    def test_streaming_cap_enforces_actual_bytes_written(self):
        """The cap counts bytes actually written, independent of any
        declared size — defense-in-depth against header manipulation."""
        from legacylens.ingestion.extractor import _CappedWriter

        writer = _CappedWriter(limit_bytes=1024 * 1024)
        src, dst = io.BytesIO(b"\x00" * (2 * 1024 * 1024)), io.BytesIO()
        with pytest.raises(ArchiveBombError, match="ceiling"):
            writer.copy(src, dst)

    def test_failed_extraction_cleans_destination(self, tmp_path, monkeypatch):
        archive = write_zip(
            tmp_path / "ok.zip", {"a.txt": b"aaa", "b.txt": b"bbb"}
        )

        def explode(self, src, dst):
            raise ArchiveBombError("simulated mid-extraction failure")

        from legacylens.ingestion import extractor as extractor_mod

        monkeypatch.setattr(extractor_mod._CappedWriter, "copy", explode)
        dest = tmp_path / "out"
        with pytest.raises(ArchiveBombError):
            extract_zip(archive, dest, settings())
        assert not dest.exists()  # no half-extracted residue in workspace

    def test_extracts_nested_structure(self, tmp_path):
        archive = write_zip(
            tmp_path / "ok.zip",
            {
                "src/main/App.java": b"class App {}",
                "src/test/AppTest.java": b"class AppTest {}",
                "pom.xml": b"<project/>",
            },
        )
        dest = tmp_path / "out"
        count = extract_zip(archive, dest, settings())
        assert count == 3
        assert (dest / "src/main/App.java").read_bytes() == b"class App {}"


class TestLanguageDetection:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("src/app.py", "python"),
            ("Main.java", "java"),
            ("web/index.tsx", "typescript"),
            ("Dockerfile", "dockerfile"),
            ("docker/Dockerfile.prod", "dockerfile"),
            ("Makefile", "makefile"),
            ("schema.sql", "sql"),
            ("unknown.xyz", None),
        ],
    )
    def test_extension_and_filename_map(self, path, expected):
        assert detect_language(path) == expected

    def test_shebang_sniffing(self):
        assert detect_language("scripts/deploy", b"#!/usr/bin/env python3\n") == "python"
        assert detect_language("scripts/run", b"#!/bin/bash\nset -e\n") == "shell"


class TestInventory:
    def make_project(self, root: Path) -> None:
        (root / "src").mkdir(parents=True)
        (root / "src/app.py").write_text("print('hi')")
        (root / "node_modules/lib").mkdir(parents=True)
        (root / "node_modules/lib/index.js").write_text("junk")
        (root / ".git").mkdir()
        (root / ".git/HEAD").write_text("ref: refs/heads/main")
        (root / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")

    def test_ignored_dirs_and_binary_flagging(self, tmp_path):
        self.make_project(tmp_path)
        records = build_inventory(tmp_path)
        paths = [r.path for r in records]
        assert "src/app.py" in paths
        assert all("node_modules" not in p and ".git" not in p for p in paths)

        png = next(r for r in records if r.path == "logo.png")
        assert png.is_binary and png.language is None

        py = next(r for r in records if r.path == "src/app.py")
        assert py.language == "python"
        assert py.sha256 and len(py.sha256) == 64


class TestProjectIngestor:
    def test_archive_end_to_end(self, tmp_path):
        archive = write_zip(
            tmp_path / "proj.zip",
            {"app/main.py": b"print('x')", "requirements.txt": b"flask==0.12\n"},
        )
        ingestor = ProjectIngestor(settings(workspace_dir=tmp_path / "ws"))
        ctx = ingestor.ingest_archive(archive)

        assert ctx.project_id.startswith("proj-")
        assert ctx.root.is_dir()
        assert {f.path for f in ctx.files} == {"app/main.py", "requirements.txt"}
        assert ctx.files_by_language("python")[0].path == "app/main.py"

    def test_directory_ingestion(self, tmp_path):
        (tmp_path / "repo").mkdir()
        (tmp_path / "repo/main.go").write_text("package main")
        ctx = ProjectIngestor(settings()).ingest_directory(
            tmp_path / "repo", project_id="proj-fixed"
        )
        assert ctx.project_id == "proj-fixed"
        assert ctx.files[0].language == "go"

    def test_empty_project_rejected(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(IngestionError, match="no analyzable files"):
            ProjectIngestor(settings()).ingest_directory(tmp_path / "empty")

    def test_missing_archive_rejected(self, tmp_path):
        with pytest.raises(IngestionError, match="not found"):
            ProjectIngestor(settings()).ingest_archive(tmp_path / "ghost.zip")
