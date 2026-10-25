"""Downloader control-flow fixtures; no live HTTP, model calls, or rendering.

The small annual CSV below is a transport/parser fixture, not a complete
scientific dataset. Tests that mock validation explicitly exercise orchestration
only; they do not claim to validate the scientific input contract.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest import mock
import urllib.error

from scripts import fetch_noaa_oisst as downloader


def annual_fixture(year):
    return ("time,zlev,latitude,longitude,sst\n"
            "UTC,m,degrees_north,degrees_east,degree_C\n"
            f"{year}-01-01T12:00:00Z,0,18.125,114.125,25.0\n"
            f"{year}-01-01T12:00:00Z,0,15.125,114.125,26.0\n").encode("utf-8")


class FetchDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        downloader.STOP.clear()
        # Every test fails if it accidentally reaches the real network helper.
        self.network = mock.patch.object(downloader, "fetch_bytes",
                                         side_effect=AssertionError("live HTTP is forbidden in tests"))
        self.fetch_bytes = self.network.start()

    def tearDown(self):
        self.network.stop()
        downloader.STOP.clear()
        self.temporary.cleanup()

    def args(self, **changes):
        values = dict(data_dir=self.root, workers=1, timeout=60, attempts=3,
                      budget_seconds=1200, offline=False, refresh=False,
                      no_gap_fixture=False, no_historical_repair=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def cache(self, year, body=None):
        body = annual_fixture(year) if body is None else body
        target = self.raw / f"oisst_v21_{year}_two_points.csv"
        target.write_bytes(body)
        provenance = target.with_suffix(".source.json")
        info = {"dataset_id": downloader.DATASET, "file": target.name,
                "sha256": hashlib.sha256(body).hexdigest(),
                "source_url": "https://example.invalid/TEST-FIXTURE-ONLY",
                "retrieved_at": "2026-01-01T00:00:00+00:00", "records": 2}
        provenance.write_text(json.dumps(info), encoding="utf-8")
        return target, provenance

    def test_verified_monthly_files_skip_all_network_work(self):
        # Validation is mocked here: this asserts only that a successful local
        # validation short-circuits downloads and clears an obsolete failure.
        validation = [{"point_id": point, "complete": True, "monthly_rows": 528}
                      for point in ("A", "B")]
        failure = self.root / "download_failures.json"
        failure.write_text('{"status":"old-error"}', encoding="utf-8")
        with mock.patch.object(downloader, "local_validation", return_value=validation) as validate, \
                mock.patch.object(downloader, "create_gap_fixture", return_value={"fixture": "test-only"}) as gap, \
                mock.patch.object(downloader, "obtain_complete_year") as obtain, \
                mock.patch.object(downloader.concurrent.futures, "ThreadPoolExecutor") as pool:
            result = downloader.prepare(self.args())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "local-cache")
        validate.assert_called_once_with(self.root.resolve())
        gap.assert_called_once_with(self.root.resolve())
        obtain.assert_not_called()
        pool.assert_not_called()
        self.fetch_bytes.assert_not_called()
        self.assertFalse(failure.exists())
        self.assertEqual(json.loads((self.root / "data_validation.json").read_text(encoding="utf-8")), validation)

    def test_offline_without_monthly_data_or_raw_cache_never_downloads(self):
        with mock.patch.object(downloader, "obtain_complete_year") as obtain, \
                mock.patch.object(downloader.concurrent.futures, "ThreadPoolExecutor") as pool:
            result = downloader.prepare(self.args(offline=True))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["category"], "offline_unavailable")
        self.assertEqual(len(result["missing_years"]), 44)
        obtain.assert_not_called()
        pool.assert_not_called()
        self.fetch_bytes.assert_not_called()

    def test_first_year_source_failure_does_not_queue_remaining_years(self):
        failure = downloader.SourceFetchError(2025, downloader.DATASET, [
            {"category": "timeout", "retryable": True, "message": "测试超时",
             "action": "测试提示", "host": "example.invalid", "attempt": 1}])
        with mock.patch.object(downloader, "obtain_complete_year", side_effect=failure) as obtain, \
                mock.patch.object(downloader.concurrent.futures, "ThreadPoolExecutor") as pool:
            result = downloader.prepare(self.args())
        self.assertEqual(result["phase"], "first-year")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failed_year"], 2025)
        self.assertEqual(result["completed_years"], [])
        self.assertEqual(obtain.call_count, 1)
        self.assertEqual(obtain.call_args.args[0], 2025)
        pool.assert_not_called()
        self.fetch_bytes.assert_not_called()
        saved = json.loads((self.root / "download_failures.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["error"]["category"], "timeout")

    def test_http_400_is_tried_once_per_source_without_backoff(self):
        error = urllib.error.HTTPError("https://example.invalid/request", 400,
                                       "TEST ONLY", {}, None)
        with mock.patch.object(downloader, "fetch", side_effect=error) as fetch, \
                mock.patch.object(downloader.STOP, "wait", return_value=False) as wait, \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(downloader.SourceFetchError) as caught:
                downloader.obtain_year(2025, self.raw, 60, False, attempts=4)
        self.assertEqual(fetch.call_count, len(downloader.BASES))
        self.assertTrue(all(item["http_status"] == 400 and not item["retryable"]
                            for item in caught.exception.failures))
        wait.assert_not_called()

    def test_transient_timeouts_retry_then_cache_only_successful_response(self):
        response = annual_fixture(2025)
        errors_then_success = [TimeoutError("TEST ONLY") for _ in downloader.BASES] + [response]
        with mock.patch.object(downloader, "fetch", side_effect=errors_then_success) as fetch, \
                mock.patch.object(downloader.STOP, "wait", return_value=False) as wait, \
                redirect_stderr(io.StringIO()):
            year, rows, info = downloader.obtain_year(2025, self.raw, 60, False, attempts=3)
        self.assertEqual(year, 2025)
        self.assertEqual(len(rows), 2)  # Deliberately partial parser fixture.
        self.assertEqual(fetch.call_count, len(downloader.BASES) + 1)
        wait.assert_called_once_with(1)
        self.assertEqual(info["sha256"], hashlib.sha256(response).hexdigest())
        self.assertEqual((self.raw / info["file"]).read_bytes(), response)
        self.assertEqual(json.loads((self.raw / info["file"]).with_suffix(".source.json").read_text(encoding="utf-8")), info)

    def test_corrupt_cache_refetches_only_that_year(self):
        good, provenance = self.cache(2024)
        before = (good.read_bytes(), provenance.read_bytes())
        bad, _ = self.cache(2025)
        bad.write_bytes(b"INTERRUPTED TEST CACHE; NOT NOAA DATA")
        with mock.patch.object(downloader, "fetch", return_value=annual_fixture(2025)) as fetch, \
                redirect_stderr(io.StringIO()):
            downloader.obtain_year(2025, self.raw, 60, False)
            downloader.obtain_year(2024, self.raw, 60, False)
        fetch.assert_called_once()
        self.assertIn("2025-01-01", fetch.call_args.args[0])
        self.assertEqual((good.read_bytes(), provenance.read_bytes()), before)
        self.assertEqual(bad.read_bytes(), annual_fixture(2025))

    def test_offline_corrupt_cache_is_not_silently_repaired(self):
        bad, _ = self.cache(2025)
        bad.write_bytes(b"BROKEN TEST CACHE")
        with self.assertRaises(downloader.DataResponseError):
            downloader.obtain_year(2025, self.raw, 60, False, offline=True)
        self.fetch_bytes.assert_not_called()

    def test_tls_failure_does_not_retry_historical_dataset_on_same_servers(self):
        detail = downloader.classify_error(ssl.SSLCertVerificationError(1, "TEST CERTIFICATE ERROR"))
        failure = downloader.SourceFetchError(1982, downloader.DATASET, [detail])
        with mock.patch.object(downloader, "obtain_year", side_effect=failure) as obtain:
            with self.assertRaises(downloader.SourceFetchError):
                downloader.obtain_complete_year(1982, self.raw, 60, False, True)
        self.assertEqual(obtain.call_count, 1)
        self.assertNotIn("dataset", obtain.call_args.kwargs)

    def test_offline_rebuild_accepts_only_documented_pre2016_historical_cache(self):
        target, provenance = self.cache(1982)
        info = json.loads(provenance.read_text(encoding="utf-8"))
        old_target = self.raw / "oisst_v2_1982_two_points.csv"
        old_provenance = old_target.with_suffix(".source.json")
        target.rename(old_target)
        info.update(dataset_id="ncdcOisst2Agg", file=old_target.name)
        old_provenance.write_text(json.dumps(info), encoding="utf-8")
        provenance.unlink()
        self.assertTrue(downloader.available_year_cache(self.raw, 1982, allow_historical=True))
        self.assertFalse(downloader.available_year_cache(self.raw, 1982))
        _, rows, sources = downloader.obtain_complete_year(1982, self.raw, 60, False, True, offline=True)
        self.assertEqual(len(rows), 2)  # Partial parser fixture, not complete input.
        self.assertEqual(sources[0]["dataset_id"], "ncdcOisst2Agg")
        self.assertEqual(sources[0]["used_record_count"], 2)
        with self.assertRaises(downloader.DataResponseError):
            downloader.obtain_complete_year(1982, self.raw, 60, False, False, offline=True)
        with self.assertRaises(downloader.DataResponseError):
            downloader.obtain_complete_year(2016, self.raw, 60, False, True, offline=True)
        self.fetch_bytes.assert_not_called()

    def test_main_stdout_is_one_json_object_and_progress_is_stderr(self):
        # This is an output-contract test: annual acquisition and scientific
        # aggregation are mocked, and the period is shortened to one year.
        validation = [{"point_id": point, "complete": True, "monthly_rows": 528}
                      for point in ("A", "B")]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(downloader, "START_YEAR", 2025), \
                mock.patch.object(downloader, "END_YEAR", 2025), \
                mock.patch.object(downloader, "obtain_complete_year",
                                  return_value=(2025, [], [{"file": "TEST-ONLY.csv"}])), \
                mock.patch.object(downloader, "build_point", side_effect=validation), \
                mock.patch.object(downloader.concurrent.futures, "ThreadPoolExecutor"), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = downloader.main(["--data-dir", str(self.root), "--no-gap-fixture"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "ok")
        self.assertEqual(len(stdout.getvalue().strip().splitlines()), 1)
        self.assertIn("2025: 0 daily point records", stderr.getvalue())
        self.fetch_bytes.assert_not_called()

    def test_main_errors_and_saved_diagnostics_do_not_echo_proxy_credentials(self):
        secret = "TEST_PROXY_PASSWORD_NEVER_LOG"
        error = urllib.error.HTTPError(f"https://test-user:{secret}@example.invalid/", 407,
                                       f"TEST ONLY {secret}", {}, None)
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(downloader, "fetch", side_effect=error), \
                mock.patch.object(downloader.concurrent.futures, "ThreadPoolExecutor") as pool, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = downloader.main(["--data-dir", str(self.root)])
        self.assertEqual(code, 2)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["error"]["failures"][0]["category"], "proxy_auth")
        pool.assert_not_called()
        saved = "".join((self.root / name).read_text(encoding="utf-8")
                        for name in ("download_status.json", "download_failures.json"))
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue() + saved)
        self.assertNotIn("test-user", stdout.getvalue() + stderr.getvalue() + saved)


if __name__ == "__main__":
    unittest.main()
