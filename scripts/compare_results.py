#!/usr/bin/env python3
"""Check that a style-only change preserved both numerical result files."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('first', type=Path, help='第一次成功运行的输出目录')
    parser.add_argument('second', type=Path, help='第二次成功运行的输出目录')
    args = parser.parse_args()
    try:
        first = json.loads((args.first / 'report.json').read_text(encoding='utf-8'))
        second = json.loads((args.second / 'report.json').read_text(encoding='utf-8'))
        if first.get('status') != 'ok' or second.get('status') != 'ok':
            raise ValueError('只能比较两次成功运行。')
        for key in ('input_sha256', 'baseline', 'method_version', 'script_sha256'):
            if first.get(key) != second.get(key) or key not in first:
                raise ValueError(f'两次运行的 {key} 不同或缺失，不是仅样式变化。')
        checks = []
        for name in ('result.csv', 'climatology.csv'):
            a, b = (args.first / name).read_bytes(), (args.second / name).read_bytes()
            checks.append({'file': name, 'identical': a == b,
                           'first_sha256': hashlib.sha256(a).hexdigest(),
                           'second_sha256': hashlib.sha256(b).hexdigest()})
        ok = all(check['identical'] for check in checks)
        print(json.dumps({'status': 'ok' if ok else 'error', 'checks': checks}, ensure_ascii=False, indent=2))
        return 0 if ok else 2
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'error', 'issues': [str(exc)]}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
