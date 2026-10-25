#!/usr/bin/env python3
"""Prepare two point files from NOAA PSL's published OISST monthly means.

This is an official NOAA fallback for networks that cannot reach the NOAA
ERDDAP mirrors. It downloads one small, point-subset response, verifies the
coordinates and 528 monthly timestamps, and records the exact source and
checksum. It never disables TLS validation or fabricates missing values.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    from . import fetch_noaa_oisst as base
except ImportError:
    import fetch_noaa_oisst as base


PSL_BASE = "https://psl.noaa.gov/thredds/dodsC/Datasets/noaa.oisst.v2.highres/sst.mon.mean.nc.ascii"
PSL_QUERY = "sst[4:1:531][420:12:432][456]"
PSL_URL = PSL_BASE + "?" + quote(PSL_QUERY, safe=":(),")
DATASET = "NOAA_PSL_OISST_MONTHLY"
TIME_EPOCH = dt.datetime(1800, 1, 1, tzinfo=dt.timezone.utc)
VALUE_LINE = re.compile(r"^\[(\d+)\]\[(\d+)\],\s*([^\s]+)\s*$")


def _parse_list(text: str, label: str) -> list[float]:
    match = re.search(rf"{re.escape(label)}\[\d+\]\s*\n([^\n]+)", text)
    if not match:
        raise ValueError(f"PSL 响应缺少 {label} 坐标数组")
    try:
        values = [float(item.strip()) for item in match.group(1).split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError(f"PSL 响应的 {label} 坐标数组无效") from exc
    return values


def parse_response(body: bytes) -> dict[str, list[tuple[dt.date, float]]]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise base.DataResponseError("NOAA PSL 响应不是 UTF-8 文本") from exc
    if "Proxy Error" in text or "<html" in text.lower():
        raise base.DataResponseError("NOAA PSL 返回了代理或 HTML 错误页")
    times = _parse_list(text, "sst.time")
    lats = _parse_list(text, "sst.lat")
    lons = _parse_list(text, "sst.lon")
    if len(times) != 528 or lats != [15.125, 18.125] or lons != [114.125]:
        raise base.DataResponseError("NOAA PSL 响应的时间或格点范围不符合预期")
    values: dict[tuple[int, int], float] = {}
    for line in text.splitlines():
        match = VALUE_LINE.match(line.strip())
        if not match:
            continue
        time_index, lat_index = int(match.group(1)), int(match.group(2))
        if time_index >= len(times) or lat_index >= len(lats):
            raise base.DataResponseError("NOAA PSL 响应包含越界数组索引")
        try:
            value = float(match.group(3))
        except ValueError as exc:
            raise base.DataResponseError("NOAA PSL 响应包含无效海温值") from exc
        if not math.isfinite(value) or not -3 <= value <= 45:
            raise base.DataResponseError("NOAA PSL 响应包含缺测或超范围海温值")
        key = (time_index, lat_index)
        if key in values:
            raise base.DataResponseError("NOAA PSL 响应包含重复海温值")
        values[key] = value
    if len(values) != 528 * 2:
        raise base.DataResponseError("NOAA PSL 响应没有提供两个格点的完整 528 个月")
    result = {"A": [], "B": []}
    for index, raw_time in enumerate(times):
        try:
            when = (TIME_EPOCH + dt.timedelta(days=raw_time)).date()
        except (OverflowError, ValueError) as exc:
            raise base.DataResponseError("NOAA PSL 时间坐标无效") from exc
        expected = dt.date(base.START_YEAR + (index // 12), index % 12 + 1, 1)
        if when != expected:
            raise base.DataResponseError("NOAA PSL 月份不连续或不在 1982-01 至 2025-12")
        result["B"].append((when, values[(index, 0)]))
        result["A"].append((when, values[(index, 1)]))
    return result


def write_csv(path: Path, rows: list[tuple[dt.date, float]], lat: float, lon: float) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["month", "lat", "lon", "sst_mean_c", "n_valid_days", "n_expected_days"])
        for when, value in rows:
            days = calendar.monthrange(when.year, when.month)[1]
            writer.writerow([when.strftime("%Y-%m"), lat, lon, f"{value:.10f}", days, days])


def build_point(point_id: str, rows: list[tuple[dt.date, float]], data: Path, source: dict) -> dict:
    lat, lon = base.POINTS[point_id]
    path = data / f"point_{point_id}_monthly.csv"
    write_csv(path, rows, lat, lon)
    metadata = {
        "point_id": point_id,
        "units": "degree_Celsius",
        "lat": lat,
        "lon": lon,
        "start_month": "1982-01",
        "end_month": "2025-12",
        "source_url": PSL_URL,
        "source_title": "NOAA PSL OISST v2 high-resolution published monthly mean",
        "product": "NOAA 0.25-degree Daily Optimum Interpolation Sea Surface Temperature (OISST), v2.1 product family; NOAA PSL published monthly means",
        "product_url": base.PRODUCT_PAGE,
        "dataset_doi": base.PRODUCT_DOI,
        "retrieved_at": source["retrieved_at"],
        "generated_at": base.NOW(),
        "sha256": base.sha256(path),
        "monthly_count": 528,
        "complete": True,
        "source_frequency": "monthly_published",
        "daily_coverage_verified": False,
        "aggregation": "NOAA PSL published monthly OISST means; this fallback does not locally re-average daily files or interpolate values.",
        "historical_source_policy": "No mirror substitution or interpolation was used; values came from the official NOAA PSL monthly product response.",
        "quality_rules": {"valid_range_degree_C": [-3, 45], "required_monthly_coverage_fraction": 1.0},
        "representation": "One 0.25-degree grid cell in a blended, interpolated SST analysis product; not an in-situ station or South China Sea spatial average.",
        "license_url": PSL_URL,
        "license_note": "NOAA data may be used and redistributed with attribution; preserve the cited product and source URL.",
        "citation": "Huang et al. (2021), Improvements of the Daily Optimum Interpolation Sea Surface Temperature (DOISST) Version 2.1, Journal of Climate 34, 2923–2939, https://doi.org/10.1175/JCLI-D-20-0166.1",
        "source_files": [source],
    }
    (data / f"point_{point_id}.metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "point_id": point_id,
        "complete": True,
        "present_days": None,
        "expected_days": None,
        "monthly_rows": 528,
        "source_frequency": "monthly_published",
        "missing_dates": [],
        "invalid_dates": [],
        "incomplete_months": [],
    }


def prepare(args):
    base.STOP.clear()
    data = args.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True)
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    raw_path = raw / "oisst_psl_monthly_1982_2025.ascii"
    provenance_path = raw_path.with_suffix(".source.json")
    started = base.NOW()
    deadline = time.monotonic() + args.budget_seconds
    try:
        body = None
        source = None
        if raw_path.exists() and provenance_path.exists() and not args.refresh:
            info = json.loads(provenance_path.read_text(encoding="utf-8"))
            raw_body = raw_path.read_bytes()
            if info.get("sha256") == hashlib.sha256(raw_body).hexdigest() and info.get("source_url") == PSL_URL:
                body, source = raw_body, info
        if body is None:
            last = None
            for attempt in range(1, args.attempts + 1):
                if time.monotonic() >= deadline:
                    raise base.NetworkDeadlineExceeded("下载时间预算已到")
                try:
                    body = base.fetch_bytes(PSL_URL, args.timeout, base.STOP, deadline=deadline)
                    parse_response(body)
                    break
                except Exception as exc:
                    last = exc
                    detail = base.classify_error(exc)
                    if not detail["retryable"] or attempt >= args.attempts:
                        raise
                    base.STOP.wait(min(2 ** (attempt - 1), 8))
            if body is None:
                raise last or RuntimeError("NOAA PSL 没有返回数据")
            base.atomic_bytes(raw_path, body)
            source = {"source_url": PSL_URL, "retrieved_at": base.NOW(),
                      "dataset_id": DATASET, "file": raw_path.name,
                      "sha256": hashlib.sha256(body).hexdigest(), "records": 1056,
                      "role": "official_noaa_psl_published_monthly"}
            base.save_json(provenance_path, source)
        values = parse_response(body)
        validation = [build_point(point, values[point], data, source) for point in ("A", "B")]
        base.save_json(data / "data_validation.json", validation)
        fixture = None if args.no_gap_fixture else base.create_gap_fixture(data)
        (data / "download_failures.json").unlink(missing_ok=True)
        result = {"status": "ok", "mode": "psl-monthly", "validation": validation,
                  "fixture": fixture, "source": PSL_URL,
                  "note": "已复核 NOAA PSL 官方发布的 528 个月均值；本次未下载逐日文件。"}
        base.save_json(data / "download_status.json", {**result, "phase": "complete",
                      "started_at": started, "updated_at": base.NOW(), "completed_years": list(range(base.START_YEAR, base.END_YEAR + 1)),
                      "source_hosts": ["psl.noaa.gov"]})
        return result
    except Exception as exc:
        detail = base.error_details(exc)
        result = {"status": "error", "mode": "psl-monthly", "error": detail,
                  "phase": "download", "started_at": started, "updated_at": base.NOW(),
                  "completed_years": [], "source_hosts": ["psl.noaa.gov"]}
        base.save_json(data / "download_status.json", result)
        base.save_json(data / "download_failures.json", result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--budget-seconds", type=int, default=1200)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-gap-fixture", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 120 or not 1 <= args.attempts <= 4 or not 60 <= args.budget_seconds <= 1500:
        parser.error("timeout: 1..120; attempts: 1..4; budget-seconds: 60..1500")
    result = prepare(args)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
