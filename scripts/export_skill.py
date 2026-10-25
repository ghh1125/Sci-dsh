#!/usr/bin/env python3
"""Backward-compatible explicit local entry; the Harness uses skill_lifecycle."""
import sys
from skill_lifecycle import main

if __name__ == '__main__':
    extra = sys.argv[1:]
    if '--output' not in extra and not any(arg.startswith('--output=') for arg in extra):
        extra = ['--output', 'dist/ocean-sst-analysis.zip', *extra]
    sys.argv = [sys.argv[0], 'export', '--source', 'installed', '--name', 'ocean-sst-analysis', *extra]
    raise SystemExit(main())
