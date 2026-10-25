#!/usr/bin/env python3
"""Compare real successful runs for style invariance or baseline algebra."""
import argparse
import calendar
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import fmean

TOLERANCE_C = 1e-9
HASH_PATTERN = re.compile(r'[0-9a-f]{64}\Z')
RESULT_COLUMNS = {'month', 'lat', 'lon', 'sst_mean_c', 'climatology_c', 'anomaly_c',
                  'n_valid_days', 'n_expected_days'}


def report_at(folder):
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    if not isinstance(report, dict) or report.get('status') != 'ok':
        raise ValueError('只能比较两次成功运行。')
    for field in ('input_sha256', 'metadata_sha256', 'script_sha256'):
        if not isinstance(report.get(field), str) or not HASH_PATTERN.fullmatch(report[field]):
            raise ValueError(f'成功报告缺少有效的 {field}。')
    if not isinstance(report.get('method_version'), str) or not report['method_version']:
        raise ValueError('成功报告缺少方法版本。')
    baseline = report.get('baseline')
    if not isinstance(baseline, list) or len(baseline) != 2 or not all(type(y) is int for y in baseline) or not (1 <= baseline[0] <= baseline[1] <= 9999) or baseline[1] - baseline[0] != 29:
        raise ValueError('运行报告必须声明连续完整的 30 年基准期。')
    return report


def table(path, key, required):
    with path.open(encoding='utf-8', newline='') as file:
        reader = csv.DictReader(file)
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not required.issubset(header):
            raise ValueError(f'{path.name} 的字段缺失或重复。')
        rows = list(reader)
    values = {row[key]: row for row in rows}
    if len(rows) != len(values) or not rows:
        raise ValueError(f'{path.name} 为空或存在重复索引。')
    return values


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('结果表包含无效数字。') from exc
    if not math.isfinite(number):
        raise ValueError('结果表存在非有限数值。')
    return number


def parse_month(label):
    if not isinstance(label, str) or not re.fullmatch(r'\d{4}-\d{2}', label):
        raise ValueError('结果表月份必须是 YYYY-MM。')
    year, month = map(int, label.split('-'))
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        raise ValueError('结果表月份无效。')
    return year, month


def validate_run(folder, report):
    """Recheck each run independently, so relabelled/copied outputs cannot pass."""
    result = table(folder / 'result.csv', 'month', RESULT_COLUMNS)
    climate = table(folder / 'climatology.csv', 'calendar_month',
                    {'calendar_month', 'climatology_c', 'sample_count'})
    if set(climate) != {str(m) for m in range(1, 13)}:
        raise ValueError('气候态必须完整包含 1 至 12 月。')
    coverage = report.get('coverage')
    if not isinstance(coverage, list) or len(coverage) != 2:
        raise ValueError('运行报告缺少覆盖期。')
    sy, sm = parse_month(coverage[0])
    ey, em = parse_month(coverage[1])
    start, end = sy * 12 + sm - 1, ey * 12 + em - 1
    if start > end or end - start > 2400:
        raise ValueError('运行报告覆盖期无效。')
    expected = {f'{index // 12:04d}-{index % 12 + 1:02d}' for index in range(start, end + 1)}
    if set(result) != expected or type(report.get('row_count')) is not int or len(result) != report['row_count']:
        raise ValueError('结果月份不连续、覆盖期不一致或行数与报告不符。')
    point = report.get('point')
    if not isinstance(point, dict) or not {'lat', 'lon'}.issubset(point):
        raise ValueError('运行报告缺少格点信息。')
    lat, lon = finite(point['lat']), finite(point['lon'])
    if not (-90 <= lat <= 90 and -180 <= lon <= 360):
        raise ValueError('运行报告的格点坐标无效。')
    first, last = report['baseline']
    baseline_values = {m: [] for m in range(1, 13)}
    parsed = {}
    for label, row in result.items():
        year, month = parse_month(label)
        days = calendar.monthrange(year, month)[1]
        if int(row['n_valid_days']) != days or int(row['n_expected_days']) != days:
            raise ValueError(f'{label} 的日覆盖不完整。')
        if abs(finite(row['lat']) - lat) > 1e-7 or abs(finite(row['lon']) - lon) > 1e-7:
            raise ValueError(f'{label} 的格点与报告不一致。')
        sst = finite(row['sst_mean_c'])
        if not -3 <= sst <= 45:
            raise ValueError(f'{label} 的 SST 超出产品核查范围。')
        parsed[label] = (year, month, sst, finite(row['climatology_c']), finite(row['anomaly_c']))
        if first <= year <= last:
            baseline_values[month].append(sst)
    recomputed = {}
    maximum_climate_error = 0.0
    for month in range(1, 13):
        if len(baseline_values[month]) != 30 or int(climate[str(month)]['sample_count']) != 30:
            raise ValueError(f'声明基准期的 {month} 月必须恰有 30 个有效月均。')
        recomputed[month] = fmean(baseline_values[month])
        difference = abs(finite(climate[str(month)]['climatology_c']) - recomputed[month])
        maximum_climate_error = max(maximum_climate_error, difference)
    if maximum_climate_error > TOLERANCE_C:
        raise ValueError('气候态不符合报告声明的基准期；不能只更换基准标签。')
    maximum_anomaly_error = 0.0
    for label, (_, month, sst, row_climate, anomaly) in parsed.items():
        declared_climate = finite(climate[str(month)]['climatology_c'])
        if abs(row_climate - declared_climate) > TOLERANCE_C:
            raise ValueError(f'{label} 的结果气候态列与 climatology.csv 不一致。')
        maximum_anomaly_error = max(maximum_anomaly_error, abs(anomaly - (sst - declared_climate)))
    if maximum_anomaly_error > TOLERANCE_C:
        raise ValueError('异常列不满足 anomaly = SST - 同月气候态。')
    return result, climate, {'climatology_recalculation_max_error_c': maximum_climate_error,
                             'anomaly_definition_max_error_c': maximum_anomaly_error}


def compare(first_dir, second_dir, mode='style'):
    first_dir, second_dir = Path(first_dir), Path(second_dir)
    first, second = report_at(first_dir), report_at(second_dir)
    invariant = ['input_sha256', 'metadata_sha256', 'method_version', 'script_sha256']
    if mode == 'style':
        invariant += ['baseline']
    elif mode != 'baseline':
        raise ValueError('比较模式必须为 style 或 baseline。')
    for key in invariant:
        if key not in first or first[key] != second.get(key):
            raise ValueError(f'两次运行的 {key} 不同或缺失，不能按 {mode} 模式比较。')
    a, ca, first_checks = validate_run(first_dir, first)
    b, cb, second_checks = validate_run(second_dir, second)
    if a.keys() != b.keys() or first['point'] != second['point']:
        raise ValueError('两次结果的月份索引或格点不一致。')
    if mode == 'style':
        checks = []
        for name in ('result.csv', 'climatology.csv'):
            a, b = (first_dir / name).read_bytes(), (second_dir / name).read_bytes()
            checks.append({'file': name, 'identical': a == b,
                           'first_sha256': hashlib.sha256(a).hexdigest(),
                           'second_sha256': hashlib.sha256(b).hexdigest()})
        ok = all(check['identical'] for check in checks)
        return {'status': 'ok' if ok else 'error', 'mode': mode, 'checks': checks,
                'independent_run_checks': [first_checks, second_checks]}
    max_sst = 0.0
    max_identity = 0.0
    for month, row in a.items():
        other = b[month]
        m = str(int(month.split('-')[1]))
        if int(ca[m]['sample_count']) != 30 or int(cb[m]['sample_count']) != 30:
            raise ValueError('气候态样本数不是30。')
        old_c, new_c = finite(ca[m]['climatology_c']), finite(cb[m]['climatology_c'])
        max_sst = max(max_sst, abs(finite(row['sst_mean_c']) - finite(other['sst_mean_c'])))
        identity = finite(row['anomaly_c']) - finite(other['anomaly_c']) - (new_c - old_c)
        max_identity = max(max_identity, abs(identity))
    ok = max_sst == 0.0 and max_identity <= TOLERANCE_C
    return {'status': 'ok' if ok else 'error', 'mode': mode, 'row_count': len(a),
            'baselines': [first['baseline'], second['baseline']],
            'same_baseline': first['baseline'] == second['baseline'],
            'sst_max_abs_change_c': max_sst, 'baseline_identity_max_error_c': max_identity,
            'independent_run_checks': [first_checks, second_checks],
            'identity': 'anomaly_first - anomaly_second = climatology_second - climatology_first',
            'note': '基准变化的调整量可以因日历月份不同而不同；不是统一平移一个常数。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('first', type=Path)
    parser.add_argument('second', type=Path)
    parser.add_argument('--mode', choices=['style', 'baseline'], default='style')
    args = parser.parse_args()
    try:
        result = compare(args.first, args.second, args.mode)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'ok' else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'error', 'issues': [str(exc)]}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
