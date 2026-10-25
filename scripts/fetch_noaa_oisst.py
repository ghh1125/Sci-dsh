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
import json
import math
from pathlib import Path
import ssl
import threading
import urllib.parse
import urllib.request

POINTS = {"A": (18.125, 114.125), "B": (15.125, 114.125)}
START_YEAR, END_YEAR = 1982, 2025
BASES = ("https://upwell.pfeg.noaa.gov/erddap",
         "https://coastwatch.pfeg.noaa.gov/erddap")
DATASET = "ncdcOisst21Agg"
PRODUCT_PAGE = "https://www.ncei.noaa.gov/products/optimum-interpolation-sst"
PRODUCT_DOI = "https://doi.org/10.25921/RE9P-PT57"
NOW = lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
STOP = threading.Event()


class DownloadCancelled(Exception):
    pass


def check_cancelled():
    if STOP.is_set():
        raise DownloadCancelled("Download cancelled; no further requests will be started")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tls_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url: str, timeout: int) -> bytes:
    check_cancelled()
    req = urllib.request.Request(url, headers={"User-Agent": "Scientific-Agent-OISST-Teaching-Demo/1.0"})
    with urllib.request.urlopen(req, context=tls_context(), timeout=timeout) as response:
        chunks = []
        while True:
            check_cancelled()
            chunk = response.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
        check_cancelled()
        return b"".join(chunks)


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
                dataset: str = DATASET):
    check_cancelled()
    version_tag = "v21" if dataset == DATASET else "v2"
    target = raw / f"oisst_{version_tag}_{year}_two_points.csv"
    provenance = target.with_suffix(".source.json")
    if target.exists() and provenance.exists() and not refresh:
        body = target.read_bytes()
        info = json.loads(provenance.read_text(encoding="utf-8"))
        if info.get("sha256") != sha256(target):
            raise ValueError(f"Cached source checksum mismatch: {target}")
        return year, parse_response(body, year), info
    errors = []
    for attempt in range(2):
        for base in BASES:
            url = query_url(base, year, dataset)
            try:
                body = fetch(url, timeout)
                parsed = parse_response(body, year)
                check_cancelled()
                target.write_bytes(body)
                info = {"source_url": url, "retrieved_at": NOW(),
                        "dataset_id": dataset, "file": target.name,
                        "sha256": sha256(target), "records": len(parsed),
                        "response_filtered_to_requested_year": True}
                provenance.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
                return year, parsed, info
            except DownloadCancelled:
                raise
            except Exception as exc:
                errors.append(f"{base}: {type(exc).__name__}: {exc}")
        if attempt == 0:
            if STOP.wait(2):
                raise DownloadCancelled("Download cancelled")
    raise RuntimeError(f"Cannot fetch {year}: " + "; ".join(errors))


def obtain_complete_year(year, raw, timeout, refresh, historical_repair):
    """Use documented identical v2 history only for missing pre-2016 records.

    NOAA documents v2.1 SST as identical to v2 for Sep 1981–Dec 2015.
    Existing v2.1 values are never replaced. All available overlap is checked;
    the original responses and the dates actually used from v2 are recorded.
    This does not apply to 2016 or later, when v2.1 changed the SST analysis.
    """
    primary_error = None
    try:
        _, primary, primary_info = obtain_year(year, raw, timeout, refresh)
        primary_info = dict(primary_info, role="primary_v2.1")
        sources = [primary_info]
    except DownloadCancelled:
        raise
    except Exception as exc:
        if year >= 2016 or not historical_repair:
            raise
        primary_error = str(exc)
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
                                                     dataset="ncdcOisst2Agg")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--refresh", action="store_true", help="Re-fetch and overwrite cached public source responses")
    parser.add_argument("--no-historical-repair", action="store_true",
                        help="Do not supplement pre-2016 mirror gaps with NOAA's documented identical v2 history")
    parser.add_argument("--no-gap-fixture", action="store_true",
                        help="Skip the deliberately incomplete B_gap teaching copy")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 8 or args.timeout < 1:
        parser.error("--workers must be 1..8 and --timeout must be positive")
    data = args.data_dir.resolve()
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    combined, sources, failures = [], [], []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    future_years = {}
    try:
        future_years = {pool.submit(obtain_complete_year, year, raw, args.timeout,
                                   args.refresh, not args.no_historical_repair): year
                        for year in range(START_YEAR, END_YEAR + 1)}
        for future in concurrent.futures.as_completed(future_years):
            year = future_years[future]
            try:
                _, rows, info = future.result()
                combined.extend(rows)
                sources.extend(info)
                print(f"{year}: {len(rows)} daily point records", flush=True)
            except Exception as exc:
                failures.append({"year": year, "error": str(exc)})
                print(f"{year}: FAILED {exc}", flush=True)
    except KeyboardInterrupt:
        STOP.set()
        for future in future_years:
            future.cancel()
        print("Cancelled. No new requests will start. Waiting for any in-flight HTTPS operation to reach its timeout; completed raw caches are retained.", flush=True)
        raise SystemExit(130)
    finally:
        # Running network calls have a finite timeout and check STOP before any
        # retry or file write. Never leave a background downloader after Ctrl-C.
        pool.shutdown(wait=True, cancel_futures=True)
    if failures:
        (data / "download_failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")
        raise SystemExit("Some source requests failed; cached successes are retained. Re-run to resume.")
    sources.sort(key=lambda s: s["file"])
    report = [build_point(p, combined, sources, data) for p in POINTS]
    (data / "data_validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for point in report:
        print(json.dumps({k: v for k, v in point.items() if not isinstance(v, list)}), flush=True)
    if not all(p["complete"] for p in report):
        raise SystemExit("NOAA mirror has missing/invalid dates. See data_validation.json; incomplete months remain blank.")
    failures_path = data / "download_failures.json"
    if failures_path.exists():
        failures_path.unlink()
    if not args.no_gap_fixture:
        print(json.dumps(create_gap_fixture(data)), flush=True)


if __name__ == "__main__":
    main()
