"""Offline/cache behavior for the NOAA PSL monthly fallback."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import fetch_noaa_psl_monthly as downloader


def psl_fixture():
    times = []
    for year in range(1982, 2026):
        for month in range(1, 13):
            times.append((dt.date(year, month, 1) - dt.date(1800, 1, 1)).days)
    lines = [
        "sst.time[528]",
        ",".join(str(value) for value in times),
        "sst.lat[2]",
        "15.125, 18.125",
        "sst.lon[1]",
        "114.125",
    ]
    for index in range(528):
        lines.extend([f"[{index}][0], 26.0", f"[{index}][1], 25.0"])
    return ("\n".join(lines) + "\n").encode("utf-8")


class FetchPslMonthlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, **changes):
        values = dict(data_dir=self.root, workers=1, timeout=60, attempts=1,
                      budget_seconds=120, offline=False, refresh=False,
                      no_gap_fixture=True)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_offline_without_verified_raw_cache_reports_local_error(self):
        with mock.patch.object(downloader.base, "fetch_bytes",
                               side_effect=AssertionError("live HTTP is forbidden in tests")):
            result = downloader.prepare(self.args(offline=True))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["category"], "offline_unavailable")
        self.assertFalse((self.root / "point_A_monthly.csv").exists())

    def test_offline_reuses_verified_raw_cache_without_network(self):
        raw = self.root / "raw"
        raw.mkdir()
        body = psl_fixture()
        raw_path = raw / "oisst_psl_monthly_1982_2025.ascii"
        raw_path.write_bytes(body)
        source = {"source_url": downloader.PSL_URL,
                  "sha256": hashlib.sha256(body).hexdigest(),
                  "retrieved_at": "2026-01-01T00:00:00+00:00"}
        raw_path.with_suffix(".source.json").write_text(
            json.dumps(source), encoding="utf-8")
        with mock.patch.object(downloader.base, "fetch_bytes",
                               side_effect=AssertionError("live HTTP is forbidden in tests")):
            result = downloader.prepare(self.args(offline=True))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "psl-monthly")
        self.assertTrue((self.root / "point_A_monthly.csv").exists())
        metadata = json.loads((self.root / "point_A.metadata.json").read_text(encoding="utf-8"))
        self.assertFalse(metadata["daily_coverage_verified"])
        self.assertEqual(metadata["source_frequency"], "monthly_published")


if __name__ == "__main__":
    unittest.main()
