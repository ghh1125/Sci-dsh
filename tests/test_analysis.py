"""Analytic fixtures are only for tests; delivered demo data come from NOAA."""
import calendar
import csv
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sst_analysis", ROOT / "skills/ocean-sst-analysis/scripts/analyze_sst.py")
sst = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sst)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.input = self.root / "analytic_fixture.csv"
        self.meta = self.root / "analytic_fixture.metadata.json"
        self.rows = []
        for year in range(1982, 2026):
            for month in range(1, 13):
                days = calendar.monthrange(year, month)[1]
                self.rows.append({"month": f"{year}-{month:02d}", "lat": 18.125, "lon": 114.125,
                                  "sst_mean_c": 24 + .2 * month + .01 * (year - 1982),
                                  "n_valid_days": days, "n_expected_days": days})
        self.metadata = {"units": "degree_Celsius", "lat": 18.125, "lon": 114.125,
                         "start_month": "1982-01", "end_month": "2025-12",
                         "source_url": "https://example.invalid/analytic-test-only",
                         "product": "ANALYTIC TEST FIXTURE; NOT OBSERVATIONS"}
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        with self.input.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        self.metadata["sha256"] = hashlib.sha256(self.input.read_bytes()).hexdigest()
        self.meta.write_text(json.dumps(self.metadata))

    def load(self, baseline=(1991, 2020)):
        return sst.load_and_validate(self.input, self.meta, baseline)

    def test_known_climatology_and_anomaly(self):
        rows, _, _ = self.load()
        result, climate, residual = sst.calculate(rows, (1991, 2020))
        # Average year in a complete 1991–2020 interval is analytically 2005.5.
        self.assertAlmostEqual(climate[7], 24 + .2 * 7 + .01 * (2005.5 - 1982), places=12)
        july_2025 = next(row for row in result if row["month"] == "2025-07")
        self.assertAlmostEqual(july_2025["anomaly_c"], .01 * (2025 - 2005.5), places=12)
        self.assertLess(residual, 1e-12)

    def test_baseline_change_identity(self):
        rows, _, _ = self.load()
        first, climate1, _ = sst.calculate(rows, (1991, 2020))
        second, climate2, _ = sst.calculate(rows, (1982, 2011))
        for a, b in zip(first, second):
            month = a["calendar_month"]
            self.assertEqual(a["sst_mean_c"], b["sst_mean_c"])
            self.assertAlmostEqual(a["anomaly_c"] - b["anomaly_c"],
                                   climate2[month] - climate1[month], places=12)

    def test_missing_baseline_month_fails_without_figure(self):
        self.rows = [row for row in self.rows if row["month"] != "2000-02"]
        self.save()
        output = self.root / "failed"
        with self.assertRaises(sst.DataError) as error:
            sst.analyze(self.input, self.meta, (1991, 2020), output)
        self.assertIn("2000-02", str(error.exception))
        self.assertIn("29", str(error.exception))
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["artifacts"], [])
        self.assertEqual([p.name for p in output.iterdir()], ["report.json"])

    def test_duplicate_month_rejected(self):
        self.rows.append(dict(self.rows[0]))
        self.save()
        with self.assertRaisesRegex(sst.DataError, "月份重复"):
            self.load()

    def test_missing_day_rejected(self):
        self.rows[0]["n_valid_days"] -= 1
        self.save()
        with self.assertRaisesRegex(sst.DataError, "日覆盖不完整"):
            self.load()

    def test_unit_not_guessed(self):
        self.metadata.pop("units")
        self.save()
        with self.assertRaisesRegex(sst.DataError, "degree_Celsius"):
            self.load()

    def test_changed_data_requires_matching_provenance(self):
        self.input.write_bytes(self.input.read_bytes() + b"\n")
        with self.assertRaisesRegex(sst.DataError, "校验值"):
            self.load()

    def test_bad_baseline_rejected(self):
        with self.assertRaisesRegex(sst.DataError, "30 年"):
            self.load((2000, 2020))

    def test_nonfinite_data_rejected(self):
        self.rows[0]["sst_mean_c"] = float("nan")
        self.save()
        with self.assertRaisesRegex(sst.DataError, "非有限"):
            self.load()

    def test_output_reuse_does_not_overwrite(self):
        output = self.root / "already_used"
        output.mkdir()
        marker = output / "existing.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(sst.DataError, "不是空目录"):
            sst.analyze(self.input, self.meta, (1991, 2020), output)
        self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
