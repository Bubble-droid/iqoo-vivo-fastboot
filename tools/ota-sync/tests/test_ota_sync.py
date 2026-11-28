"""Tests for the OTA rescue tool (no device required)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
for path in (str(TOOL_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from common.util import parse_size  # noqa: E402
from ota_sync import detect  # noqa: E402
from ota_sync.session import SyncSession, safe_bound  # noqa: E402
from ota_sync.state import SyncState, load_state, save_state  # noqa: E402


class SegmentLayoutTest(unittest.TestCase):
    def test_observed_five_segment_layout(self) -> None:
        total = 5_568_031_751
        segments = detect.derive_segments(total, 5)
        self.assertEqual(
            [(segment.start, segment.end) for segment in segments],
            [
                (0, 1_113_606_351),
                (1_113_606_351, 2_227_212_702),
                (2_227_212_702, 3_340_819_053),
                (3_340_819_053, 4_454_425_404),
                (4_454_425_404, 5_568_031_751),
            ],
        )

    def test_single_segment(self) -> None:
        segments = detect.derive_segments(100, 1)
        self.assertEqual([(s.start, s.end) for s in segments], [(0, 100)])

    def test_equal_segments(self) -> None:
        segments = detect.derive_segments(90, 3)
        self.assertEqual(
            [(s.start, s.end) for s in segments],
            [(0, 31), (31, 62), (62, 90)],
        )

    def test_zero_total_rejected(self) -> None:
        with self.assertRaises(ValueError):
            detect.derive_segments(0, 5)


class SelectionTest(unittest.TestCase):
    def test_total_priority(self) -> None:
        self.assertEqual(
            detect.pick_total(7, 100, 200, 300, 400), (7, "--total")
        )
        self.assertEqual(
            detect.pick_total(None, -1, 200, 300, 400),
            (200, "remote.xml totalBytes"),
        )
        self.assertEqual(
            detect.pick_total(None, -1, None, None, 400),
            (400, "HTTP Content-Length"),
        )
        self.assertEqual(detect.pick_total(None, -1, None, None, None), (0, ""))

    def test_segment_count(self) -> None:
        self.assertEqual(
            detect.pick_segment_count(7, 5, 100)[0], 7
        )
        self.assertEqual(
            detect.pick_segment_count(None, 5, 100)[0], 5
        )
        self.assertEqual(
            detect.pick_segment_count(None, 0, 2 << 20)[0],
            detect.DEFAULT_CORE_SIZE,
        )
        self.assertEqual(detect.pick_segment_count(None, 0, 1000)[0], 1)

    def test_md5_and_sha256(self) -> None:
        md5 = "8d8da289ee627b52c4a315a703ed5b7b"
        self.assertEqual(
            detect.pick_md5(md5, None, None), (md5, "--md5")
        )
        self.assertEqual(
            detect.pick_md5(None, md5, None),
            (md5, "HTTP x-kss-meta-md5"),
        )
        self.assertEqual(detect.pick_md5(None, "not-a-md5", None), (None, ""))

        sha = "a" * 64
        self.assertEqual(
            detect.pick_sha256(sha, None), (sha, "--sha256")
        )

    def test_path_helpers(self) -> None:
        self.assertEqual(
            detect.package_from_path(
                "/data/user_de/0/com.bbk.updater/databases/downloads.db"
            ),
            "com.bbk.updater",
        )
        self.assertEqual(
            detect.package_from_path(
                "/data/vivo-updater/com.bbk.updater/OTAPackage/x.zip"
            ),
            "com.bbk.updater",
        )
        self.assertEqual(
            detect.keep_basename("20240101abc.zip"),
            "ota-keep-20240101abc.zip",
        )
        self.assertEqual(
            detect.keep_basename("ota-keep-20240101abc.zip"),
            "ota-keep-20240101abc.zip",
        )

    def test_parse_size_is_reused(self) -> None:
        self.assertEqual(parse_size("5284163108"), 5_284_163_108)
        self.assertEqual(parse_size("5GiB"), 5 * 1024 ** 3)


class DatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "downloads.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create(self, schema: str, insert: str) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(schema + insert)
            connection.commit()
        finally:
            connection.close()

    def test_downloads_table(self) -> None:
        self._create(
            """
            create table downloads(
                _id integer primary key autoincrement,
                uri text, _data text, status integer,
                total_bytes integer, current_bytes integer,
                complete_threads integer, download_type integer,
                check_sum text
            );
            """,
            """
            insert into downloads(
                uri, _data, status, total_bytes, current_bytes,
                complete_threads, download_type, check_sum
            ) values(
                'https://example/ota.zip',
                '/data/vivo-updater/com.bbk.updater/OTAPackage/ota.zip',
                193, -1, 0, 0, 5, null
            );
            """,
        )
        analysis = detect.analyze_db(str(self.db_path))
        self.assertEqual(analysis["table"], "downloads")
        self.assertEqual(analysis["row"]["download_type"], 5)
        self.assertEqual(analysis["row"]["status"], 193)
        self.assertEqual(
            analysis["row"]["_data"],
            "/data/vivo-updater/com.bbk.updater/OTAPackage/ota.zip",
        )

    def test_fallback_table(self) -> None:
        self._create(
            """
            create table download_tasks(
                id integer primary key,
                total_bytes integer,
                current_bytes integer,
                data text
            );
            """,
            "insert into download_tasks values(1, 100, 40, '/x.zip');",
        )
        analysis = detect.analyze_db(str(self.db_path))
        self.assertEqual(analysis["table"], "download_tasks")
        self.assertEqual(analysis["row"]["total_bytes"], 100)


class SafeBoundTest(unittest.TestCase):
    def test_aligned_segment(self) -> None:
        segment = detect.Segment(0, 4 * 65536)
        self.assertEqual(safe_bound(segment, 1), 0)
        self.assertEqual(safe_bound(segment, 2), 65536)
        self.assertEqual(safe_bound(segment, 4), 3 * 65536)
        # A complete segment still reports within one block of its end, which
        # is what the completion check expects.
        self.assertGreaterEqual(
            safe_bound(segment, 4), segment.end - 65536
        )

    def test_unaligned_start_is_not_trusted_early(self) -> None:
        segment = detect.Segment(100, 10 * 65536)
        # ceil(100 / 65536) == 1, so the first checked block is 1.
        self.assertEqual(safe_bound(segment, 1), 100)
        self.assertEqual(safe_bound(segment, 2), 65536)
        self.assertEqual(safe_bound(segment, 3), 2 * 65536)

    def test_clamped_to_segment_end(self) -> None:
        segment = detect.Segment(0, 1000)
        self.assertEqual(safe_bound(segment, 99), 1000)


class StateTest(unittest.TestCase):
    def test_roundtrip_and_inode_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            segments = [(0, 100), (100, 200)]
            state = SyncState.fresh(42, 200, segments)
            state.copied = [50, 120]
            save_state(path, state)

            loaded = load_state(path, 42, 200, segments)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.copied, [50, 120])

            self.assertIsNone(load_state(path, 43, 200, segments))
            self.assertIsNone(load_state(path, 42, 201, segments))

    def test_clamp(self) -> None:
        state = SyncState(
            inode=1,
            total=100,
            segments=[(0, 50), (50, 100)],
            copied=[-10, 200],
        )
        state.clamp()
        self.assertEqual(state.copied, [0, 100])


class ZipTest(unittest.TestCase):
    def test_zip_crc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            good = Path(temp_dir) / "good.zip"
            with zipfile.ZipFile(good, "w", zipfile.ZIP_STORED) as archive:
                archive.writestr("a.txt", "hello")
            self.assertTrue(SyncSession.zip_test(good))

            bad = Path(temp_dir) / "bad.zip"
            bad.write_bytes(good.read_bytes()[:-8])
            self.assertFalse(SyncSession.zip_test(bad))


if __name__ == "__main__":
    unittest.main()
