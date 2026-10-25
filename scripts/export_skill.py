#!/usr/bin/env python3
"""Package the reusable Skill only; never include credentials, data, or outputs."""
import argparse
from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / 'skills' / 'ocean-sst-analysis'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=root / 'dist' / 'ocean-sst-analysis.zip')
    args = parser.parse_args()
    if not (source / 'SKILL.md').is_file() or not (source / 'scripts/analyze_sst.py').is_file():
        raise SystemExit('Skill 缺少入口或分析脚本。')
    if args.output.exists():
        raise SystemExit('目标已存在，请更换 --output 文件名以保留原版本。')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # The allow-list excludes caches and any accidentally colocated secret files.
    files = sorted(p for p in source.rglob('*') if p.is_file()
                   and p.suffix in {'.md', '.py', '.mplstyle', '.json'}
                   and '__pycache__' not in p.parts and not any(part.startswith('.') for part in p.relative_to(source).parts))
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, str(Path(source.name) / file.relative_to(source)))
    print(f'Skill 已打包：{args.output.resolve()}（{len(files)} 个文件）')


if __name__ == '__main__':
    main()
