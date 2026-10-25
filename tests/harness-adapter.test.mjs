import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { findDshRuntime, configureToolsImport } from '../scripts/runtime-paths.mjs';
configureToolsImport(findDshRuntime());
const { buildInvocation } = await import('../harness/ocean-tools.mjs');

test('cases map to fixed files and independent style does not alter baseline', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-test-'));
  try {
    for (const c of ['A', 'B', 'missing-month']) {
      const one = buildInvocation(root, 'python3', { case: c, style: 'presentation', output: 'result' });
      assert.equal(one.argv[0], 'python3');
      assert.deepEqual(one.baseline, [1991, 2020]);
      assert.ok(one.argv.includes(path.join(fs.realpathSync(root), 'data', c === 'missing-month' ? 'point_B_gap_demo.csv' : `point_${c}_monthly.csv`)));
      assert.equal(one.output, path.join(fs.realpathSync(root), 'outputs/result'));
    }
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('invalid case, paths and baseline fail before launching anything', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-test-'));
  try {
    for (const args of [{ case: 'other', style: 'default' }, { case: 'A', style: 'default', output: '../outside' }, { case: 'A', style: 'default', output: '/tmp/outside' }, { case: 'A', style: 'default', output: 'x; touch pwned' }, { case: 'A', style: 'default', baseline_start: 1991, baseline_end: 2019 }]) {
      assert.throws(() => buildInvocation(root, 'python3', args));
    }
    fs.symlinkSync(os.tmpdir(), path.join(root, 'outputs'));
    assert.throws(() => buildInvocation(root, 'python3', { case: 'A', style: 'default' }), /符号链接/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('a nonempty output is rejected without changing or exposing old artifacts', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-test-'));
  try {
    const output = path.join(root, 'outputs/old-run');
    fs.mkdirSync(output, { recursive: true });
    fs.writeFileSync(path.join(output, 'old-result.csv'), 'unchanged');
    assert.throws(() => buildInvocation(root, 'python3', { case: 'A', style: 'default', output: 'old-run' }), /非空/);
    assert.equal(fs.readFileSync(path.join(output, 'old-result.csv'), 'utf8'), 'unchanged');
    assert.deepEqual(fs.readdirSync(output), ['old-result.csv']);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
