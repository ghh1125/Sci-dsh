#!/usr/bin/env python3
"""Reproducible single-grid-cell monthly SST analysis for DeepSeek Harness.

Only matplotlib/numpy are needed for rendering. Validation and calculations use
the Python standard library; no model-generated values enter the computation.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

METHOD_VERSION = "1.0.0"
REQUIRED_COLUMNS = {
    "month", "lat", "lon", "sst_mean_c", "n_valid_days", "n_expected_days"
}


class DataError(ValueError):
    def __init__(self, issues):
        self.issues = issues if isinstance(issues, list) else [str(issues)]
        super().__init__("; ".join(self.issues))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_month(value):
    if not re.fullmatch(r"\d{4}-\d{2}", str(value)):
        raise ValueError(f"月份格式应为 YYYY-MM：{value!r}")
    year, month = map(int, value.split("-"))
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        raise ValueError(f"无效月份：{value}")
    return year, month


def month_range(start, end):
    sy, sm = parse_month(start)
    ey, em = parse_month(end)
    first, last = sy * 12 + sm - 1, ey * 12 + em - 1
    if first > last:
        raise ValueError("起始月份晚于结束月份")
    if last - first > 2400:
        raise ValueError("单次示例最多支持 200 年月数据")
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(first, last + 1)]


def load_and_validate(input_path, metadata_path, baseline):
    input_path, metadata_path = Path(input_path), Path(metadata_path)
    try:
        raw = input_path.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataError(f"无法读取输入文件：{exc}") from exc
    if not isinstance(metadata, dict):
        raise DataError("数据说明必须是 JSON 对象")
    issues = []
    if metadata.get("units") != "degree_Celsius":
        issues.append("数据说明 units 必须明确为 degree_Celsius；不猜测或自动转换单位")
    for key in ("lat", "lon", "start_month", "end_month", "source_url", "product", "sha256"):
        if key not in metadata or metadata[key] in (None, ""):
            issues.append(f"数据说明缺少字段：{key}")
    digest = hashlib.sha256(raw).hexdigest()
    if metadata.get("sha256") != digest:
        issues.append("CSV 校验值与数据说明不一致；请核对数据版本")
    try:
        expected = month_range(metadata["start_month"], metadata["end_month"])
        site_lat, site_lon = float(metadata["lat"]), float(metadata["lon"])
        if not (math.isfinite(site_lat) and math.isfinite(site_lon)):
            raise ValueError("坐标不是有限数")
        if not (-90 <= site_lat <= 90 and -180 <= site_lon <= 360):
            raise ValueError("坐标超出地理范围")
        metadata = {**metadata, "lat": site_lat, "lon": site_lon}
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError(issues + [f"数据说明范围或坐标无效：{exc}"]) from exc
    first, last = baseline
    if last - first != 29:
        issues.append("本示例要求完整的连续 30 年基准期")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise DataError(issues + ["CSV 缺少字段：" + ", ".join(sorted(missing))])
        rows = []
        seen = set()
        for line, record in enumerate(reader, start=2):
            try:
                label = record["month"].strip()
                year, month = parse_month(label)
                if label in seen:
                    issues.append(f"月份重复：{label}")
                seen.add(label)
                lat, lon = float(record["lat"]), float(record["lon"])
                temperature = float(record["sst_mean_c"])
                valid = int(record["n_valid_days"])
                declared = int(record["n_expected_days"])
                days = calendar.monthrange(year, month)[1]
                if not all(map(math.isfinite, [lat, lon, temperature])):
                    raise ValueError("存在非有限数值")
                if abs(lat - site_lat) > 1e-7 or abs(lon - site_lon) > 1e-7:
                    issues.append(f"{label} 坐标与数据说明不一致")
                if declared != days or valid != days:
                    issues.append(f"{label} 日覆盖不完整或天数错误：有效 {valid}，声明 {declared}，应有 {days}")
                # This range is a plausibility screen, not automatic outlier removal.
                if not -3 <= temperature <= 45:
                    issues.append(f"{label} 海温 {temperature}°C 超出本产品核查范围，请核对填充值/单位")
                rows.append({"month": label, "year": year, "calendar_month": month,
                             "sst_mean_c": temperature, "lat": lat, "lon": lon,
                             "n_valid_days": valid, "n_expected_days": days})
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                issues.append(f"CSV 第 {line} 行无效：{exc}")
    except UnicodeError as exc:
        raise DataError("CSV 不是有效 UTF-8 文本") from exc
    missing_months = sorted(set(expected) - seen)
    extra_months = sorted(seen - set(expected))
    if missing_months:
        issues.append("缺少月份：" + ", ".join(missing_months[:24]))
    if extra_months:
        issues.append("出现声明范围外的月份：" + ", ".join(extra_months[:24]))
    if len(rows) != len(expected):
        issues.append(f"行数不符合声明覆盖期：读取 {len(rows)}，预期 {len(expected)}")
    counts = {m: sum(first <= r["year"] <= last and r["calendar_month"] == m for r in rows)
              for m in range(1, 13)}
    for month, count in counts.items():
        if count != 30:
            issues.append(f"基准期 {first}–{last} 的 {month} 月仅有 {count} 个值，要求 30 个")
    if issues:
        raise DataError(issues)
    return sorted(rows, key=lambda r: r["month"]), metadata, digest


def calculate(rows, baseline):
    first, last = baseline
    climatology = {
        month: fmean(r["sst_mean_c"] for r in rows
                     if first <= r["year"] <= last and r["calendar_month"] == month)
        for month in range(1, 13)
    }
    results = []
    for row in rows:
        normal = climatology[row["calendar_month"]]
        results.append({**row, "climatology_c": normal,
                        "anomaly_c": row["sst_mean_c"] - normal})
    residual = max(abs(fmean(r["anomaly_c"] for r in results
                            if first <= r["year"] <= last and r["calendar_month"] == m))
                   for m in range(1, 13))
    if residual > 1e-9:
        raise DataError(f"内部验收失败：基准期各月份异常均值偏离零（{residual}）")
    return results, climatology, residual


def write_csv(path, fields, records):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def render(results, climatology, metadata, baseline, output, style):
    # Keep transient font/cache files in this run, never in the user's home.
    os.environ.setdefault("MPLCONFIGDIR", str(output / ".mpl-cache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import numpy as np

    presentation = style == "presentation"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12 if presentation else 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.labelcolor": "#213547", "text.color": "#213547",
                         "xtick.color": "#536372", "ytick.color": "#536372",
                         "svg.fonttype": "none", "pdf.fonttype": 42})
    color_sst, color_normal = "#007A87", "#84919C"
    warm, cold = ("#D85A41", "#337BA5") if presentation else ("#8A97A3", "#8A97A3")
    last_year = max(r["year"] for r in results)
    latest = [r for r in results if r["year"] == last_year]
    fig, axes = plt.subplots(2, 1, figsize=(12.6, 8.6),
                             gridspec_kw={"height_ratios": [1, 1.25]})
    fig.patch.set_facecolor("#FFFFFF")
    fig.subplots_adjust(left=.085, right=.975, top=.80, bottom=.14, hspace=.47)
    fig.text(.085, .947, "SOUTH CHINA SEA  /  SCIENTIFIC AGENT DEMO", fontsize=10,
             weight="bold", color="#007A87")
    fig.text(.085, .898, "Sea-surface temperature, with a traceable baseline", fontsize=20,
             weight="bold")
    first, last = baseline
    fig.text(.085, .854,
             f"Grid cell: {metadata['lat']:.3f}°N, {metadata['lon']:.3f}°E  |  "
             f"Baseline: {first}–{last}  |  Monthly means", fontsize=11, color="#536372")
    ax = axes[0]
    months = [r["calendar_month"] for r in latest]
    ax.plot(range(1, 13), [climatology[m] for m in range(1, 13)],
            linestyle="--", linewidth=2, color=color_normal, label=f"{first}–{last} climatology")
    ax.plot(months, [r["sst_mean_c"] for r in latest], marker="o", markersize=4,
            linewidth=2.3, color=color_sst, label=f"{last_year} SST")
    ax.set(xlim=(.7, 12.3), ylabel="SST (°C)")
    ax.set_xticks(range(1, 13), [calendar.month_abbr[m] for m in range(1, 13)])
    ax.set_title(f"a   {last_year} seasonal cycle", loc="left", fontsize=12, weight="bold", pad=12)
    ax.grid(axis="y", color="#E7ECF0", linewidth=.7)
    ax.legend(frameon=False, ncol=2, loc="best", fontsize=9)
    ax = axes[1]
    dates = [datetime(r["year"], r["calendar_month"], 15) for r in results]
    anomaly = np.array([r["anomaly_c"] for r in results])
    ax.fill_between(dates, 0, anomaly, where=anomaly >= 0, interpolate=True, color=warm, alpha=.82)
    ax.fill_between(dates, 0, anomaly, where=anomaly <= 0, interpolate=True, color=cold, alpha=.82)
    ax.plot(dates, anomaly, color="#314754", linewidth=.65, alpha=.8)
    ax.axhline(0, color="#344857", linewidth=.9)
    bound = math.ceil(max(abs(anomaly.min()), abs(anomaly.max())) * 2) / 2 + .25
    ax.set(ylim=(-bound, bound), ylabel="SST anomaly (°C)", xlim=(dates[0], dates[-1]))
    ax.set_title("b   Monthly departures from the same-calendar-month baseline", loc="left",
                 fontsize=12, weight="bold", pad=12)
    ax.xaxis.set_major_locator(mdates.YearLocator(5))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(axis="y", color="#E7ECF0", linewidth=.7)
    fig.text(.085, .070, "Source: NOAA OISST (full provenance in report.json)  |  "
             f"Coverage: {results[0]['month']} to {results[-1]['month']}  |  No smoothing or gap filling",
             fontsize=9, color="#536372")
    fig.text(.085, .040, "Single-grid-cell illustration; not a South China Sea regional mean or a causal attribution.",
             fontsize=9, color="#536372")
    for extension in ("png", "svg", "pdf"):
        fig.savefig(output / f"sst_analysis.{extension}", dpi=180, facecolor="white")
    plt.close(fig)


def analyze(input_path, metadata_path, baseline, output, style="presentation"):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise DataError("输出目录不是空目录；请使用新的运行目录，避免混淆历史结果")
    output.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        rows, metadata, digest = load_and_validate(input_path, metadata_path, baseline)
        results, climatology, residual = calculate(rows, baseline)
        render(results, climatology, metadata, baseline, output, style)
        source_frequency = str(metadata.get("source_frequency", "daily_aggregated")).strip() or "daily_aggregated"
        daily_verified = metadata.get("daily_coverage_verified")
        if not isinstance(daily_verified, bool):
            daily_verified = source_frequency != "monthly_published"
        published_monthly = source_frequency == "monthly_published"
        fields = ["month", "lat", "lon", "sst_mean_c", "climatology_c", "anomaly_c",
                  "n_valid_days", "n_expected_days"]
        write_csv(output / "result.csv", fields, results)
        write_csv(output / "climatology.csv", ["calendar_month", "climatology_c", "sample_count"],
                  [{"calendar_month": m, "climatology_c": climatology[m], "sample_count": 30}
                   for m in range(1, 13)])
        latest_year = max(r["year"] for r in results)
        check = next((r for r in results if r["year"] == latest_year and r["calendar_month"] == 7), results[-1])
        report = {
            "status": "ok", "run_id": run_id, "started_at": started_at,
            "method_version": METHOD_VERSION, "style": style,
            "input_file": str(Path(input_path).resolve()), "input_sha256": digest,
            "metadata_file": str(Path(metadata_path).resolve()),
            "metadata_sha256": sha256(metadata_path), "script_sha256": sha256(__file__),
            "point": {"lat": metadata["lat"], "lon": metadata["lon"]},
            "coverage": [results[0]["month"], results[-1]["month"]],
            "baseline": list(baseline), "row_count": len(results), "source": metadata,
            "checks": {"complete_monthly_coverage": True,
                       "complete_daily_coverage": daily_verified,
                       "complete_source_coverage": True,
                       "coverage_basis": "published_monthly" if published_monthly else "daily_aggregated",
                       "baseline_count_per_month": 30,
                       "baseline_mean_anomaly_max_abs_c": residual},
            "spot_check": {k: check[k] for k in ("month", "sst_mean_c", "climatology_c", "anomaly_c")},
            "artifacts": ["sst_analysis.png", "sst_analysis.svg", "sst_analysis.pdf", "result.csv",
                          "climatology.csv", "summary.md", "report.json"],
        }
        coverage_note = ("NOAA 官方发布月平均完整；未在本地核验逐日文件或重新聚合。"
                         if published_monthly else "自然月逐日值完整后计算月平均。")
        summary = f"""# 海温分析结果\n\n本次分析已通过数据完整性与基准期检查。\n\n- 示例格点：{metadata['lat']:.3f}°N，{metadata['lon']:.3f}°E。\n- 数据覆盖：{results[0]['month']} 至 {results[-1]['month']}，共 {len(results)} 个月。\n- 覆盖依据：{coverage_note}\n- 基准期：{baseline[0]}–{baseline[1]}，每个日历月 30 个完整月均值。\n- 数据来源：[{metadata.get('source_title', metadata['product'])}]({metadata['source_url']})。\n\n## 数值抽查\n\n{check['month']} 的月均海温为 {check['sst_mean_c']:.4f} °C，同月气候态为 {check['climatology_c']:.4f} °C，两者之差为 {check['anomaly_c']:+.4f} °C。计算及验收使用未舍入数值。\n\n## 图注\n\n南海示例格点的月平均海表温度与异常。上图比较 {latest_year} 年月均海温与 {baseline[0]}–{baseline[1]} 年同月气候态；下图展示各月相对于同一基准的海温异常。月气候态为基准期内各年同一日历月月均值的等权平均。数据来自 {metadata['product']}，无平滑或缺值插补。\n\n## 解释范围\n\n这是单个格点的分析产品结果，不代表整个南海的区域平均。本示例未进行趋势显著性检验、海洋热浪判定或因果归因。相邻月份和年份也不能视为独立重复实验。\n\n## 复现\n\n方法版本：{METHOD_VERSION}；样式：{style}；运行标识：{run_id}。输入、脚本校验值及完整参数见 report.json。图形样式的改变不应改变数值结果。\n"""
        (output / "summary.md").write_text(summary, encoding="utf-8")
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception as exc:
        issues = exc.issues if isinstance(exc, DataError) else [str(exc)]
        # The directory was empty at entry. Remove only this run's known outputs
        # if rendering/writing fails, so partial scientific products are not reused.
        for name in ("sst_analysis.png", "sst_analysis.svg", "sst_analysis.pdf",
                     "result.csv", "climatology.csv", "summary.md"):
            path = output / name
            if path.exists():
                path.unlink()
        report = {"status": "error", "run_id": run_id, "started_at": started_at,
                  "method_version": METHOD_VERSION, "issues": issues, "artifacts": []}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise DataError(issues) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--baseline", nargs=2, type=int, default=[1991, 2020], metavar=("START", "END"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--style", choices=["default", "presentation"], default="presentation")
    args = parser.parse_args()
    try:
        report = analyze(args.input, args.metadata, tuple(args.baseline), args.output, args.style)
        print(json.dumps({"status": "ok", "output": str(args.output.resolve()),
                          "row_count": report["row_count"], "baseline": report["baseline"],
                          "artifacts": report["artifacts"], "spot_check": report["spot_check"]}, ensure_ascii=False))
        return 0
    except DataError as exc:
        print(json.dumps({"status": "error", "issues": exc.issues, "artifacts": []}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
