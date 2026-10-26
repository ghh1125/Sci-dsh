"""Analytical comparison fixtures only; no NOAA data, model calls, or rendering."""
import calendar
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("result_comparison", ROOT / "scripts/compare_results.py")
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)
RESULT_FIELDS = ['month', 'lat', 'lon', 'sst_mean_c', 'climatology_c', 'anomaly_c',
                 'n_valid_days', 'n_expected_days']
CLIMATE_FIELDS = ['calendar_month', 'climatology_c', 'sample_count']


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_csv(self, path, fields, rows):
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def read_csv(self, path):
        with path.open(encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))

    def run_fixture(self, name, baseline=(1991, 2020), *, offset=0.0, style='default'):
        """Use a closed-form seasonal series, independent of the analysis code.

        Each month has a different linear slope; a baseline switch therefore
        cannot be represented by a single constant for all calendar months.
        """
        folder = self.root / name
        folder.mkdir()
        center = (baseline[0] + baseline[1]) / 2
        climate = {month: 20 + .2 * month + .001 * month * (center - 1982) + offset
                   for month in range(1, 13)}
        rows = []
        for year in range(1982, 2026):
            for month in range(1, 13):
                days = calendar.monthrange(year, month)[1]
                sst = 20 + .2 * month + .001 * month * (year - 1982) + offset
                rows.append({'month': f'{year}-{month:02d}', 'lat': 18.125, 'lon': 114.125,
                             'sst_mean_c': sst, 'climatology_c': climate[month],
                             'anomaly_c': sst - climate[month],
                             'n_valid_days': days, 'n_expected_days': days})
        self.write_csv(folder / 'result.csv', RESULT_FIELDS, rows)
        self.write_csv(folder / 'climatology.csv', CLIMATE_FIELDS,
                       [{'calendar_month': month, 'climatology_c': climate[month], 'sample_count': 30}
                        for month in range(1, 13)])
        report = {'status': 'ok', 'baseline': list(baseline), 'row_count': 528,
                  'coverage': ['1982-01', '2025-12'], 'point': {'lat': 18.125, 'lon': 114.125},
                  'method_version': 'analytical-test-only', 'style': style,
                  'input_sha256': hashlib.sha256(b'ANALYTICAL INPUT FIXTURE, NOT OBSERVATIONS').hexdigest(),
                  'metadata_sha256': hashlib.sha256(b'ANALYTICAL METADATA FIXTURE').hexdigest(),
                  'script_sha256': hashlib.sha256(b'ANALYTICAL METHOD FIXTURE').hexdigest()}
        (folder / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        return folder

    def mutate_report(self, folder, **changes):
        path = folder / 'report.json'
        report = json.loads(path.read_text(encoding='utf-8'))
        report.update(changes)
        path.write_text(json.dumps(report), encoding='utf-8')

    def test_month_specific_baseline_change_passes_and_preserves_sst(self):
        first = self.run_fixture('new-normal', (1991, 2020))
        second = self.run_fixture('older-normal', (1982, 2011))
        result = comparison.compare(first, second, 'baseline')
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['same_baseline'])
        self.assertEqual(result['sst_max_abs_change_c'], 0)
        self.assertLess(result['baseline_identity_max_error_c'], 1e-12)
        a = {row['calendar_month']: float(row['climatology_c'])
             for row in self.read_csv(first / 'climatology.csv')}
        b = {row['calendar_month']: float(row['climatology_c'])
             for row in self.read_csv(second / 'climatology.csv')}
        for month in range(1, 13):
            self.assertAlmostEqual(a[str(month)] - b[str(month)], .009 * month, places=12)
        self.assertNotAlmostEqual(a['1'] - b['1'], a['12'] - b['12'])

    def test_relabeling_baseline_without_recalculation_is_rejected(self):
        first = self.run_fixture('original')
        second = self.run_fixture('copied-but-relabeled')
        self.mutate_report(second, baseline=[1982, 2011])
        with self.assertRaisesRegex(ValueError, '不能只更换基准标签'):
            comparison.compare(first, second, 'baseline')

    def test_common_anomaly_bias_cannot_cancel_in_cross_run_identity(self):
        first = self.run_fixture('first', (1991, 2020))
        second = self.run_fixture('second', (1982, 2011))
        for folder in (first, second):
            rows = self.read_csv(folder / 'result.csv')
            for row in rows:
                row['anomaly_c'] = float(row['anomaly_c']) + 0.5
            self.write_csv(folder / 'result.csv', RESULT_FIELDS, rows)
        with self.assertRaisesRegex(ValueError, 'anomaly = SST'):
            comparison.compare(first, second, 'baseline')

    def test_result_climatology_column_must_match_climatology_table(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second', (1982, 2011))
        rows = self.read_csv(second / 'result.csv')
        rows[0]['climatology_c'] = float(rows[0]['climatology_c']) + .2
        self.write_csv(second / 'result.csv', RESULT_FIELDS, rows)
        with self.assertRaisesRegex(ValueError, '气候态列'):
            comparison.compare(first, second, 'baseline')

    def test_one_constant_cannot_replace_month_specific_baseline_changes(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second', (1982, 2011))
        old_rows = self.read_csv(first / 'result.csv')
        changed_rows = self.read_csv(second / 'result.csv')
        for old, new in zip(old_rows, changed_rows):
            new['anomaly_c'] = float(old['anomaly_c']) + .009
        self.write_csv(second / 'result.csv', RESULT_FIELDS, changed_rows)
        with self.assertRaisesRegex(ValueError, 'anomaly = SST'):
            comparison.compare(first, second, 'baseline')

    def test_sst_change_fails_even_when_both_runs_are_internally_consistent(self):
        first = self.run_fixture('first')
        second = self.run_fixture('changed-input', (1982, 2011), offset=1)
        result = comparison.compare(first, second, 'baseline')
        self.assertEqual(result['status'], 'error')
        self.assertAlmostEqual(result['sst_max_abs_change_c'], 1, places=12)

    def test_style_change_requires_identical_numerical_files_and_metadata(self):
        first = self.run_fixture('first', style='default')
        second = self.run_fixture('second', style='presentation')
        result = comparison.compare(first, second, 'style')
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(all(check['identical'] for check in result['checks']))
        self.mutate_report(second, metadata_sha256=hashlib.sha256(b'changed metadata').hexdigest())
        with self.assertRaisesRegex(ValueError, 'metadata_sha256'):
            comparison.compare(first, second, 'style')

    def test_published_monthly_runs_compare_without_daily_claim(self):
        first = self.run_fixture('first', style='default')
        second = self.run_fixture('second', style='presentation')
        checks = {'complete_monthly_coverage': True, 'complete_daily_coverage': False,
                  'complete_source_coverage': True, 'coverage_basis': 'published_monthly'}
        self.mutate_report(first, checks=checks)
        self.mutate_report(second, checks=checks)
        result = comparison.compare(first, second, 'style')
        self.assertEqual(result['status'], 'ok')

    def test_style_comparison_rejects_baseline_change(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second', (1982, 2011))
        with self.assertRaisesRegex(ValueError, 'baseline'):
            comparison.compare(first, second, 'style')

    def test_missing_months_cannot_be_hidden_by_changing_report_row_count(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second', (1982, 2011))
        for folder in (first, second):
            rows = [row for row in self.read_csv(folder / 'result.csv') if row['month'] != '2025-12']
            self.write_csv(folder / 'result.csv', RESULT_FIELDS, rows)
            self.mutate_report(folder, row_count=527)
        with self.assertRaisesRegex(ValueError, '月份不连续'):
            comparison.compare(first, second, 'baseline')

    def test_duplicate_month_or_missing_climatology_is_rejected(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second', (1982, 2011))
        rows = self.read_csv(second / 'result.csv')
        self.write_csv(second / 'result.csv', RESULT_FIELDS, rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, '重复索引'):
            comparison.compare(first, second, 'baseline')
        self.write_csv(second / 'result.csv', RESULT_FIELDS, rows)
        climate = self.read_csv(second / 'climatology.csv')[:-1]
        self.write_csv(second / 'climatology.csv', CLIMATE_FIELDS, climate)
        with self.assertRaisesRegex(ValueError, '1 至 12 月'):
            comparison.compare(first, second, 'baseline')

    def test_wrong_sample_count_and_nonfinite_values_are_rejected(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second', (1982, 2011))
        climate = self.read_csv(second / 'climatology.csv')
        climate[0]['sample_count'] = 29
        self.write_csv(second / 'climatology.csv', CLIMATE_FIELDS, climate)
        with self.assertRaisesRegex(ValueError, '30 个'):
            comparison.compare(first, second, 'baseline')
        climate[0]['sample_count'] = 30
        self.write_csv(second / 'climatology.csv', CLIMATE_FIELDS, climate)
        rows = self.read_csv(second / 'result.csv')
        rows[0]['anomaly_c'] = 'nan'
        self.write_csv(second / 'result.csv', RESULT_FIELDS, rows)
        with self.assertRaisesRegex(ValueError, '非有限'):
            comparison.compare(first, second, 'baseline')

    def test_report_must_have_a_valid_30_year_baseline_and_success_status(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second')
        self.mutate_report(second, baseline=[1991, 2019])
        with self.assertRaisesRegex(ValueError, '30 年'):
            comparison.compare(first, second, 'baseline')
        self.mutate_report(second, baseline=[1991, 2020], status='error')
        with self.assertRaisesRegex(ValueError, '成功运行'):
            comparison.compare(first, second, 'baseline')

    def test_same_baseline_is_reported_explicitly(self):
        first = self.run_fixture('first')
        second = self.run_fixture('second')
        result = comparison.compare(first, second, 'baseline')
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['same_baseline'])


if __name__ == '__main__':
    unittest.main()
