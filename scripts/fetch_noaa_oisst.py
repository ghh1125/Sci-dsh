#!/usr/bin/env python3
"""Fetch two real NOAA OISST grid points and aggregate complete calendar months.

The script retrieves only small annual CSV subsets from NOAA ERDDAP, caches
the original responses, and never interpolates or fabricates missing SSTs.
It uses the Python standard library; certifi is optional for local TLS setups.
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import csv
import datetime as dt
import hashlib
import io
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.parse

if __package__:
    from .download_network import (fetch_bytes, classify_error, DownloadCancelled,
                                   NetworkDeadlineExceeded, DataResponseError)
else:
    from download_network import (fetch_bytes, classify_error, DownloadCancelled,
                                  NetworkDeadlineExceeded, DataResponseError)

POINTS = {"A": (18.125, 114.125), "B": (15.125, 114.125)}
START_YEAR, END_YEAR = 1982, 2025
BASES = ("https://upwell.pfeg.noaa.gov/erddap",
         "https://coastwatch.pfeg.noaa.gov/erddap")
DATASET = "ncdcOisst21Agg"
PRODUCT_PAGE = "https://www.ncei.noaa.gov/products/optimum-interpolation-sst"
PRODUCT_DOI = "https://doi.org/10.25921/RE9P-PT57"
NOW = lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
STOP = threading.Event()


class SourceFetchError(Exception):
    def __init__(self, year, dataset, failures):
        self.year, self.dataset, self.failures = year, dataset, failures
        super().__init__(f"{year}: 无法取得 NOAA 年度数据；请查看下载诊断。")


def atomic_bytes(path, body):
    """Promote one complete file; interrupted writes never become valid cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def error_details(exc):
    if isinstance(exc, SourceFetchError):
        categories = {item["category"] for item in exc.failures}
        same_reason = len(categories) == 1
        first = exc.failures[0] if exc.failures else {}
        return {"category": first["category"] if same_reason else "source_unavailable",
                "message": f"{exc.year}: " + first["message"] if same_reason else str(exc),
                "retryable": any(item["retryable"] for item in exc.failures),
                "action": first["action"] if same_reason else "按 failures 中的具体提示处理；修复网络后重新准备，成功缓存会保留。",
                "year": exc.year, "dataset": exc.dataset, "failures": exc.failures}
    return classify_error(exc)


def check_cancelled():
    if STOP.is_set():
        raise DownloadCancelled("Download cancelled; no further requests will be started")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url: str, timeout: int, deadline=None) -> bytes:
    return fetch_bytes(url, timeout, STOP, deadline=deadline)


def query_url(base: str, year: int, dataset: str = DATASET) -> str:
    query = (f"sst[({year}-01-01T12:00:00Z):1:({year}-12-31T12:00:00Z)]"
             "[(0.0)][(15.125):12:(18.125)][(114.125)]")
    return base + "/griddap/" + dataset + ".csv?" + urllib.parse.quote(query, safe=":(),")


def parse_response(body: bytes, year: int):
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    expected_header = ["time", "zlev", "latitude", "longitude", "sst"]
    if not rows or rows[0] != expected_header:
        raise ValueError("Unexpected NOAA CSV schema: " + repr(rows[:2]))
    if len(rows) < 3 or rows[1][-1] != "degree_C":
        raise ValueError("NOAA SST units are not explicitly degree_C")
    parsed = []
    seen = set()
    for row in rows[2:]:
        if len(row) != 5:
            raise ValueError("Malformed NOAA data row")
        date = dt.datetime.fromisoformat(row[0].replace("Z", "+00:00")).date()
        lat, lon, value = map(float, (row[2], row[3], row[4]))
        if (lat, lon) not in POINTS.values() or float(row[1]) != 0:
            raise ValueError("Unexpected coordinate in response")
        # ERDDAP may snap a requested boundary to its nearest available time.
        # A mirror can have a completely absent year. Preserve its raw response,
        # but never relabel an out-of-range date as data from the requested year.
        if date.year != year:
            continue
        key = (date, lat, lon)
        if key in seen:
            raise ValueError("Duplicate daily coordinate: " + str(key))
        seen.add(key)
        valid = math.isfinite(value) and -3 <= value <= 45 and value != -9.99
        parsed.append((date, lat, lon, value if valid else None))
    return parsed


def obtain_year(year: int, raw: Path, timeout: int, refresh: bool,
                dataset: str = DATASET, *, attempts=3, deadline=None, offline=False):
    check_cancelled()
    version_tag = "v21" if dataset == DATASET else "v2"
    target = raw / f"oisst_{version_tag}_{year}_two_points.csv"
    provenance = target.with_suffix(".source.json")
    if target.exists() and provenance.exists() and not refresh:
        try:
            body = target.read_bytes()
            info = json.loads(provenance.read_text(encoding="utf-8"))
            if info.get("sha256") != hashlib.sha256(body).hexdigest() or info.get("dataset_id") != dataset:
                raise ValueError("Cached source checksum or dataset mismatch")
            return year, parse_response(body, year), info
        except (OSError, ValueError, TypeError, AttributeError):
            if offline:
                raise DataResponseError(f"{year}: 本地年度缓存损坏，离线模式不联网修复")
            print(f"{year}: 缓存未通过校验，只重新获取这个年份。", file=sys.stderr, flush=True)
    if offline:
        raise DataResponseError(f"{year}: 缺少可验证的本地年度缓存，离线模式不下载")
    errors = []
    active = list(BASES)
    for attempt in range(attempts):
        retry = []
        for base in active:
            check_cancelled()
            if deadline is not None and time.monotonic() >= deadline:
                raise NetworkDeadlineExceeded("下载时间预算已到")
            url = query_url(base, year, dataset)
            try:
                body = fetch(url, timeout, deadline)
                try:
                    parsed = parse_response(body, year)
                except (ValueError, UnicodeError, OverflowError) as exc:
                    raise DataResponseError("NOAA 返回的数据格式、单位、日期或坐标未通过校验") from exc
                check_cancelled()
                atomic_bytes(target, body)
                info = {"source_url": url, "retrieved_at": NOW(),
                        "dataset_id": dataset, "file": target.name,
                        "sha256": hashlib.sha256(body).hexdigest(), "records": len(parsed),
                        "response_filtered_to_requested_year": True}
                save_json(provenance, info)
                return year, parsed, info
            except (DownloadCancelled, NetworkDeadlineExceeded):
                raise
            except Exception as exc:
                detail = {**classify_error(exc), "host": urllib.parse.urlsplit(base).hostname,
                          "attempt": attempt + 1}
                errors.append(detail)
                print(f"{year} {detail['host']}: {detail['message']}", file=sys.stderr, flush=True)
                if detail["retryable"]:
                    retry.append(base)
        active = retry
        if not active:
            break
        if attempt + 1 < attempts:
            delay = min(2 ** attempt, 8)
            if deadline is not None:
                delay = min(delay, max(0, deadline - time.monotonic()))
            if STOP.wait(delay):
                raise DownloadCancelled("Download cancelled")
    raise SourceFetchError(year, dataset, errors)


def obtain_complete_year(year, raw, timeout, refresh, historical_repair,
                         *, attempts=3, deadline=None, offline=False):
    """Use documented identical v2 history only for missing pre-2016 records.

    NOAA documents v2.1 SST as identical to v2 for Sep 1981–Dec 2015.
    Existing v2.1 values are never replaced. All available overlap is checked;
    the original responses and the dates actually used from v2 are recorded.
    This does not apply to 2016 or later, when v2.1 changed the SST analysis.
    """
    primary_error = None
    try:
        _, primary, primary_info = obtain_year(year, raw, timeout, refresh,
            attempts=attempts, deadline=deadline, offline=offline)
        primary_info = dict(primary_info, role="primary_v2.1")
        sources = [primary_info]
    except (DownloadCancelled, NetworkDeadlineExceeded):
        raise
    except DataResponseError:
        if not offline or year >= 2016 or not historical_repair:
            raise
        # A completed online run may have cached only v2 when the historical
        # v2.1 endpoint returned 404/410. The same documented equivalence also
        # permits a cache-only rebuild, with no network or guessed values.
        primary_error = {"category": "primary_cache_unavailable",
                         "message": "离线 v2.1 缓存缺失或未通过校验；仅核对已缓存的等价历史 v2。"}
        primary, sources = [], []
    except SourceFetchError as exc:
        # A different dataset on the same servers cannot repair TLS, DNS, proxy
        # or timeout failures. Try historical v2 only for an absent v2.1 dataset.
        absent = bool(exc.failures) and all(item.get("http_status") in (404, 410) for item in exc.failures)
        if year >= 2016 or not historical_repair or not absent:
            raise
        primary_error = error_details(exc)
        primary, sources = [], []
    first, stop = dt.date(year, 1, 1), dt.date(year + 1, 1, 1)
    expected_keys = {
        (first + dt.timedelta(days=offset), lat, lon)
        for offset in range((stop-first).days) for lat, lon in POINTS.values()
    }
    selected = {(date, lat, lon): value for date, lat, lon, value in primary}
    missing_keys = expected_keys - selected.keys()
    if missing_keys and year <= 2015 and historical_repair:
        check_cancelled()
        _, historical, historical_info = obtain_year(year, raw, timeout, refresh,
            dataset="ncdcOisst2Agg", attempts=attempts, deadline=deadline, offline=offline)
        historical_values = {(date, lat, lon): value
                             for date, lat, lon, value in historical}
        common_valid = [key for key in selected.keys() & historical_values.keys()
                        if selected[key] is not None and historical_values[key] is not None]
        max_difference = max((abs(selected[key] - historical_values[key])
                              for key in common_valid), default=None)
        if max_difference is not None and max_difference > 1e-6:
            raise ValueError(f"{year}: historical v2 and v2.1 overlap disagrees "
                             f"by {max_difference} degree_C; no records were merged")
        added = sorted(key for key in missing_keys & historical_values.keys()
                       if historical_values[key] is not None)
        for key in added:
            selected[key] = historical_values[key]
        historical_info = dict(historical_info,
            role="documented_identical_pre_2016_v2_supplement",
            equivalence_source_url=PRODUCT_PAGE,
            equivalence_scope="NOAA states SST for September 1981–December 2015 is the same in v2.1 and v2",
            overlap_valid_record_count=len(common_valid),
            overlap_max_abs_difference_degree_C=max_difference,
            used_record_count=len(added),
            used_dates_by_point={
                point_id: [date.isoformat() for date, la, lo in added if (la, lo) == coordinates]
                for point_id, coordinates in POINTS.items()
            })
        if primary_error is not None:
            historical_info["primary_request_failure"] = primary_error
        sources.append(historical_info)
    result = [(date, lat, lon, value) for (date, lat, lon), value in sorted(selected.items())]
    return year, result, sources


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def build_point(point_id, combined, sources, data):
    check_cancelled()
    lat, lon = POINTS[point_id]
    values = {d: v for d, la, lo, v in combined if (la, lo) == (lat, lon)}
    first, stop = dt.date(START_YEAR, 1, 1), dt.date(END_YEAR + 1, 1, 1)
    dates = [first + dt.timedelta(days=i) for i in range((stop-first).days)]
    missing = [d.isoformat() for d in dates if d not in values]
    invalid = [d.isoformat() for d in dates if d in values and values[d] is None]
    daily_path = data / "raw" / f"point_{point_id}_daily.csv"
    write_csv(daily_path, ["date_utc", "lat", "lon", "sst_c", "source_record_present"],
              [(d.isoformat(), lat, lon, "" if values.get(d) is None else format(values[d], ".10g"), int(d in values)) for d in dates])
    monthly_rows = []
    incomplete_months = []
    for year in range(START_YEAR, END_YEAR + 1):
        for month in range(1, 13):
            n_expected = calendar.monthrange(year, month)[1]
            month_days = [dt.date(year, month, day) for day in range(1, n_expected + 1)]
            valid = [values[d] for d in month_days if values.get(d) is not None]
            complete = len(valid) == n_expected
            month_key = f"{year}-{month:02d}"
            if not complete:
                incomplete_months.append(month_key)
            monthly_rows.append((month_key, lat, lon,
                                 f"{math.fsum(valid)/len(valid):.10f}" if complete else "",
                                 len(valid), n_expected))
    monthly_path = data / f"point_{point_id}_monthly.csv"
    write_csv(monthly_path,
              ["month", "lat", "lon", "sst_mean_c", "n_valid_days", "n_expected_days"], monthly_rows)
    meta = {
        "point_id": point_id, "units": "degree_Celsius", "lat": lat, "lon": lon,
        "start_month": f"{START_YEAR}-01", "end_month": f"{END_YEAR}-12",
        "source_url": BASES[0] + "/griddap/" + DATASET + ".html",
        "source_title": "NOAA OISST v2.1 Final daily SST, with documented identical pre-2016 v2 source records when needed",
        "product": "NOAA 0.25-degree Daily Optimum Interpolation Sea Surface Temperature (OISST), v2.1 Final; any historical v2 supplements are explicitly listed in source_files",
        "product_url": PRODUCT_PAGE, "dataset_doi": PRODUCT_DOI,
        "retrieved_at": max(s["retrieved_at"] for s in sources),
        "generated_at": NOW(), "sha256": sha256(monthly_path),
        "daily_file": str(daily_path.relative_to(data)), "daily_sha256": sha256(daily_path),
        "expected_daily_count": len(dates), "present_daily_count": len(values),
        "valid_daily_count": sum(v is not None for v in values.values()),
        "monthly_count": len(monthly_rows), "missing_dates": missing,
        "invalid_dates": invalid, "incomplete_months": incomplete_months,
        "complete": not (missing or invalid),
        "aggregation": "Arithmetic mean of all daily SST values within each UTC calendar month. Output SST is blank unless every expected day has a valid source value. No interpolation, clipping, synthetic filling, or anom-variable reuse.",
        "historical_source_policy": "Only missing pre-2016 mirror records may be supplemented from NOAA v2, whose SST is documented by NOAA as identical for September 1981–December 2015. All available overlap must agree within 0.000001 degree_C. Per-source used dates are recorded; no post-2015 v2 records are used.",
        "quality_rules": {"valid_range_degree_C": [-3, 45], "source_fill_value": -9.99,
                          "required_daily_coverage_fraction": 1.0},
        "representation": "One 0.25-degree grid cell in a blended, interpolated SST analysis product; not an in-situ station or South China Sea spatial average.",
        "license_url": BASES[0] + "/info/" + DATASET + "/index.html",
        "license_note": "NOAA ERDDAP metadata permits free use and redistribution; supplied without warranty. Preserve source attribution and dataset citation.",
        "citation": "Huang et al. (2021), Improvements of the Daily Optimum Interpolation Sea Surface Temperature (DOISST) Version 2.1, Journal of Climate 34, 2923–2939, https://doi.org/10.1175/JCLI-D-20-0166.1",
        "source_files": sources,
    }
    (data / f"point_{point_id}.metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"point_id": point_id, "complete": meta["complete"],
            "present_days": len(values), "expected_days": len(dates),
            "missing_dates": missing, "invalid_dates": invalid,
            "incomplete_months": incomplete_months, "monthly_rows": len(monthly_rows)}


def create_gap_fixture(data: Path, missing_month: str = "2000-02"):
    """Create an explicitly labelled bad-input fixture after both real sets pass.

    Only one monthly row is omitted. No SST value is altered, and the complete
    source dataset remains unchanged. This is a validation demonstration, not
    a second scientific dataset.
    """
    check_cancelled()
    original = data / "point_B_monthly.csv"
    metadata = json.loads((data / "point_B.metadata.json").read_text(encoding="utf-8"))
    if not metadata.get("complete") or metadata.get("monthly_count") != 528:
        raise ValueError("Gap fixture requires a complete, verified point B source")
    if metadata["sha256"] != sha256(original):
        raise ValueError("Point B checksum changed before fixture creation")
    with original.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames
        rows = list(reader)
    if sum(row["month"] == missing_month for row in rows) != 1:
        raise ValueError("Fixture month must occur exactly once")
    kept = [row for row in rows if row["month"] != missing_month]
    target = data / "point_B_gap_demo.csv"
    write_csv(target, header, [[row[field] for field in header] for row in kept])
    fixture_meta = dict(metadata)
    fixture_meta.update({
        "point_id": "B_gap_demo", "sha256": sha256(target), "generated_at": NOW(),
        "complete": False, "monthly_count": len(kept),
        "incomplete_months": [missing_month],
        "dataset_kind": "intentionally_incomplete_teaching_fixture",
        "parent_monthly_file": original.name,
        "parent_monthly_sha256": metadata["sha256"],
        "teaching_fixture": {
            "operation": "Omit exactly one monthly row; preserve every other row and every retained SST value",
            "removed_month": missing_month,
            "expected_behavior": "Reject as incomplete before climatology, anomaly calculation, or figure creation",
            "raw_daily_records_unchanged": True,
            "warning": "This monthly CSV was deliberately made incomplete for a failure-handling demonstration. Do not treat it as the complete scientific source data. Daily counts and raw files in inherited provenance refer to the unchanged parent source."
        }
    })
    (data / "point_B_gap_demo.metadata.json").write_text(
        json.dumps(fixture_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"fixture": target.name, "rows": len(kept), "removed_month": missing_month}


def local_validation(data):
    """Reuse verified monthly files even when raw caches were not copied.

    This calls only the shared input validator: no plotting or model calls.
    Invalid/incomplete local files never become a successful offline result.
    """
    script = Path(__file__).resolve().parents[1] / "skill-templates/ocean-sst-analysis/scripts/analyze_sst.py"
    spec = importlib.util.spec_from_file_location("sst_input_validator", script)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    report = []
    for point, coordinates in POINTS.items():
        rows, metadata, _ = validator.load_and_validate(
            data / f"point_{point}_monthly.csv", data / f"point_{point}.metadata.json", (1991, 2020))
        if (len(rows) != 528 or metadata.get("start_month") != "1982-01"
                or metadata.get("end_month") != "2025-12"
                or (metadata["lat"], metadata["lon"]) != coordinates
                or metadata.get("complete") is not True
                or metadata.get("monthly_count") != 528
                or metadata.get("product_url") != PRODUCT_PAGE
                or not isinstance(metadata.get("source_files"), list) or not metadata["source_files"]):
            raise DataResponseError("本地月数据未满足本演示的完整性和来源契约")
        expected = (dt.date(2026, 1, 1) - dt.date(1982, 1, 1)).days
        report.append({"point_id": point, "complete": True, "monthly_rows": len(rows),
                       "present_days": expected, "expected_days": expected,
                       "missing_dates": [], "invalid_dates": [], "incomplete_months": []})
    return report


def available_year_cache(raw, year, *, allow_historical=False):
    tags = ["v21"] + (["v2"] if allow_historical and year <= 2015 else [])
    return any((raw / f"oisst_{tag}_{year}_two_points.csv").is_file()
               and (raw / f"oisst_{tag}_{year}_two_points.source.json").is_file() for tag in tags)


def prepare(args):
    STOP.clear()
    data = args.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True)
    started = NOW()
    deadline = time.monotonic() + args.budget_seconds
    status_file = data / "download_status.json"
    failure_file = data / "download_failures.json"
    completed = []

    def record(result, phase):
        value = {**result, "phase": phase, "started_at": started, "updated_at": NOW(),
                 "completed_years": sorted(completed),
                 "source_hosts": [urllib.parse.urlsplit(base).hostname for base in BASES]}
        save_json(status_file, value)
        if result.get("status") == "error":
            save_json(failure_file, value)
        return value

    record({"status": "preparing", "mode": "offline" if args.offline else "download"}, "local-check")
    if not args.refresh:
        try:
            validation = local_validation(data)
        except (OSError, ValueError, TypeError, KeyError):
            validation = None
        if validation is not None:
            mode = "offline" if args.offline else "local-cache"
            try:
                fixture = None if args.no_gap_fixture else create_gap_fixture(data)
                save_json(data / "data_validation.json", validation)
                failure_file.unlink(missing_ok=True)
                return record({"status": "ok", "mode": mode,
                               "validation": validation, "fixture": fixture,
                               "note": "已复核现有 CSV、metadata、日覆盖、来源与完整基准期；本次没有联网。"}, "complete")
            except Exception as exc:
                return record({"status": "error", "mode": mode, "error": error_details(exc)}, "local-check")

    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    years = list(range(START_YEAR, END_YEAR + 1))
    # Check one missing year first rather than queue 44 years against an
    # unreachable server. Prefer the newest missing year to avoid v2 fallback.
    missing = [year for year in reversed(years) if args.refresh or not available_year_cache(
        raw, year, allow_historical=args.offline and not args.no_historical_repair)]
    first_year = missing[0] if missing else END_YEAR
    if args.offline and missing:
        return record({"status": "error", "mode": "offline", "error": {
            "category": "offline_unavailable", "message": "本地月数据未通过校验，年度原始缓存也不完整。",
            "retryable": False, "action": "从已成功准备数据的电脑复制完整 data 目录，或取消 offline 后联网准备。"},
            "missing_years": missing}, "local-check")

    def obtain(year, limit):
        return obtain_complete_year(year, raw, args.timeout, args.refresh,
            not args.no_historical_repair, attempts=args.attempts, deadline=limit, offline=args.offline)

    combined, sources, failures = [], [], []
    mode = "offline" if args.offline else "download"
    record({"status": "preparing", "mode": mode, "probe_year": first_year}, "first-year")
    try:
        _, rows, info = obtain(first_year, min(deadline, time.monotonic() + 180))
        combined.extend(rows)
        sources.extend(info)
        completed.append(first_year)
        print(f"{first_year}: {len(rows)} daily point records", file=sys.stderr, flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        STOP.set()
        detail = classify_error(DownloadCancelled()) if isinstance(exc, KeyboardInterrupt) else error_details(exc)
        return record({"status": "error", "mode": mode, "error": detail,
                       "failed_year": first_year, "remaining_years": years,
                       "note": "首个年份未取得有效响应，未排队下载其余年份。已成功的原始缓存保留。"}, "first-year")

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    futures = {}
    try:
        futures = {pool.submit(obtain, year, deadline): year for year in years if year != first_year}
        for future in concurrent.futures.as_completed(futures):
            year = futures[future]
            try:
                _, rows, info = future.result()
                combined.extend(rows)
                sources.extend(info)
                completed.append(year)
                print(f"{year}: {len(rows)} daily point records", file=sys.stderr, flush=True)
                record({"status": "preparing", "mode": mode}, "annual-data")
            except Exception as exc:
                failures.append({"year": year, **error_details(exc)})
                record({"status": "error", "mode": mode, "error": failures[0],
                        "failures": failures}, "annual-data")
                # Do not keep queueing requests after a source/network failure.
                STOP.set()
                for pending in futures:
                    pending.cancel()
                break
    except KeyboardInterrupt:
        STOP.set()
        failures.append(classify_error(DownloadCancelled()))
        for pending in futures:
            pending.cancel()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if failures:
        return record({"status": "error", "mode": mode, "error": failures[0],
                       "failures": failures, "remaining_years": sorted(set(years) - set(completed)),
                       "note": "成功缓存保留；修复网络后继续准备，不必重新下载已通过校验的年份。"}, "annual-data")
    try:
        sources.sort(key=lambda source: source["file"])
        validation = [build_point(point, combined, sources, data) for point in POINTS]
        save_json(data / "data_validation.json", validation)
        if not all(point["complete"] for point in validation):
            return record({"status": "error", "mode": mode, "validation": validation, "error": {
                "category": "incomplete_data", "message": "数据源仍有缺日或无效值，未通过完整性检查。",
                "retryable": False, "action": "检查 data_validation.json；不插值、不生成伪造海温或科学图片。"}}, "validation")
        fixture = None if args.no_gap_fixture else create_gap_fixture(data)
        failure_file.unlink(missing_ok=True)
        return record({"status": "ok", "mode": mode, "validation": validation, "fixture": fixture}, "complete")
    except Exception as exc:
        return record({"status": "error", "mode": mode, "error": error_details(exc)}, "validation")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--attempts", type=int, default=3, help="Maximum attempts per source for transient errors only")
    parser.add_argument("--budget-seconds", type=int, default=1200, help="Total network budget, shorter than the Harness tool timeout")
    parser.add_argument("--offline", action="store_true", help="Validate local monthly data or rebuild from raw caches without any network request")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch and overwrite cached public source responses")
    parser.add_argument("--no-historical-repair", action="store_true",
                        help="Do not supplement pre-2016 mirror gaps with NOAA's documented identical v2 history")
    parser.add_argument("--no-gap-fixture", action="store_true",
                        help="Skip the deliberately incomplete B_gap teaching copy")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4 or not 1 <= args.timeout <= 120 or not 1 <= args.attempts <= 4 or not 60 <= args.budget_seconds <= 1500:
        parser.error("workers: 1..4; timeout: 1..120; attempts: 1..4; budget-seconds: 60..1500")
    if args.offline and args.refresh:
        parser.error("--offline cannot be combined with --refresh")
    try:
        result = prepare(args)
    except KeyboardInterrupt:
        STOP.set()
        result = {"status": "error", "mode": "offline" if args.offline else "download",
                  "error": classify_error(DownloadCancelled())}
    except Exception as exc:
        result = {"status": "error", "mode": "offline" if args.offline else "download", "error": error_details(exc)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
