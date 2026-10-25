import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { findDshRuntime, configureToolsImport } from '../scripts/runtime-paths.mjs';
import { skillBodyHash, waitForSkillBody, withDataOperation, currentDataOperation,
  pythonEnvironment, runPython } from '../harness/tool-common.mjs';
configureToolsImport(findDshRuntime());
const { buildInvocation, makeAnalysisTool } = await import('../harness/ocean-tools.mjs');
const { makeWorkflowTools } = await import('../harness/workflow-tools.mjs');

test('missing data routes all cases to preparation without starting Python or creating failed runs', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-missing-'));
  try {
    const ctx = { subprocess: { resolveExecutable() { assert.fail('Python must not start for missing data'); } } };
    const tool = makeAnalysisTool(ctx, { projectRoot: root, python: 'python3' });
    for (const point of ['A', 'B', 'missing-month']) {
      const value = await tool.execute({ case: point }, { signal: new AbortController().signal });
      assert.equal(value.code, 'DATA_NOT_PREPARED');
      assert.equal(value.nextTool, 'sst_prepare_data');
      assert.equal(value.exitCode, null);
      assert.equal(value.missing.length, 2);
      assert.ok(value.missing.every(file => path.basename(file) === file));
      assert.deepEqual(value.artifacts, []);
    }
    assert.equal(fs.existsSync(path.join(root, 'outputs')), false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('data preparation and analysis exclude each other and always release the process lock', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-lock-'));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  try {
    const active = withDataOperation(root, 'prepare', () => gate);
    assert.equal(currentDataOperation(root), 'prepare');
    for (const kind of ['prepare', 'analyze']) {
      const blocked = await withDataOperation(root, kind, () => assert.fail('Busy operation must not run'));
      assert.equal(blocked.code, 'DATA_BUSY');
    }
    release({ ok: true });
    await active;
    assert.equal(currentDataOperation(root), null);
    await assert.rejects(withDataOperation(root, 'analyze', async () => { throw new Error('example'); }), /example/);
    assert.equal(currentDataOperation(root), null);
  } finally { release(); fs.rmSync(root, { recursive: true, force: true }); }
});

test('only the downloader gets the two explicit data-network overrides', () => {
  const source = { SCI_DSH_DATA_PROXY: 'http://proxy.example.invalid:8080',
    SCI_DSH_DATA_CA_BUNDLE: 'C:\\example\\certificates.pem', SCI_DSH_API_KEY: 'never-forward-this', OTHER: 'unused' };
  const downloader = pythonEnvironment('fetch_noaa_oisst.py', source);
  assert.equal(downloader.SCI_DSH_DATA_PROXY, source.SCI_DSH_DATA_PROXY);
  assert.equal(downloader.SCI_DSH_DATA_CA_BUNDLE, source.SCI_DSH_DATA_CA_BUNDLE);
  assert.equal(Object.hasOwn(downloader, 'SCI_DSH_API_KEY'), false);
  assert.equal(Object.hasOwn(downloader, 'OTHER'), false);
  assert.equal(Object.hasOwn(pythonEnvironment('skill_lifecycle.py', source), 'SCI_DSH_DATA_PROXY'), false);
});

test('cancelled Python keeps collected diagnostics even when its exit wait throws', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-cancel-'));
  try {
    const controller = new AbortController();
    let terminated = false;
    const ctx = { subprocess: {
      async resolveExecutable() { return 'python3'; },
      spawn() {
        controller.abort();
        return { done: Promise.resolve({ exitCode: null, signal: 'SIGTERM' }),
          terminate() { terminated = true; }, async waitForExit() { throw new Error('cancelled wait'); },
          collected: { stdout: { readFrom: () => ({ text: '', lossy: false }) },
            stderr: { readFrom: () => ({ text: 'A completed cache was retained.\n', lossy: false }) } } };
      },
    } };
    const value = await runPython(ctx, { projectRoot: root, python: 'python3' },
      { callId: 'cancel-1', signal: controller.signal }, 'fetch_noaa_oisst.py', [], 'sst_prepare_data');
    assert.equal(value.ok, false);
    assert.equal(value.error.category, 'CANCELLED');
    assert.equal(terminated, true);
    assert.match(fs.readFileSync(path.join(value.logs, 'stderr.txt'), 'utf8'), /retained/);
    assert.equal(JSON.parse(fs.readFileSync(path.join(value.logs, 'operation.json'), 'utf8')).cancelled, true);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('prepare forwards bounded offline options and preserves classified network failures', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-prepare-'));
  try {
    let captured;
    let response = { status: 'ok', mode: 'offline', validation: [
      { complete: true, monthly_rows: 528 }, { complete: true, monthly_rows: 528 }] };
    const ctx = { subprocess: {
      async resolveExecutable() { return 'python3'; },
      spawn(spec) { captured = spec; return {
        done: Promise.resolve({ exitCode: response.status === 'ok' ? 0 : 2, signal: null }),
        terminate() {}, async waitForExit() { return true; },
        collected: { stdout: { readFrom: () => ({ text: JSON.stringify(response), lossy: false }) },
          stderr: { readFrom: () => ({ text: 'progress only', lossy: false }) } },
      }; },
    } };
    const tool = makeWorkflowTools(ctx, { projectRoot: root, python: 'python3' }).find(tool => tool.name === 'sst_prepare_data');
    const exec = { signal: new AbortController().signal };
    const value = await tool.execute({ offline: true }, exec);
    assert.equal(value.ok, true);
    assert.equal(value.mode, 'offline');
    assert.equal(captured.argv[captured.argv.indexOf('--workers') + 1], '1');
    assert.equal(captured.argv[captured.argv.indexOf('--attempts') + 1], '3');
    assert.equal(captured.argv[captured.argv.indexOf('--budget-seconds') + 1], '1200');
    assert.ok(captured.argv.includes('--offline'));
    response = { status: 'error', mode: 'download', error: { category: 'TLS_CERTIFICATE',
      message: 'Certificate could not be verified.', retryable: false, action: 'Configure a trusted CA bundle.' } };
    const failed = await tool.execute({}, exec);
    assert.equal(failed.ok, false);
    assert.deepEqual(failed.error, response.error);
    assert.equal(failed.result.status, 'error');
    await assert.rejects(tool.execute({ attempts: 5 }, exec), /attempts/);
    await assert.rejects(tool.execute({ offline: true, refresh: true }, exec), /offline/);
    assert.equal(currentDataOperation(root), null);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('Skill readiness checks the discovered body and supports cancellation', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-dsh-ready-'));
  try {
    const skillFile = path.join(root, 'SKILL.md');
    const text = '---\nname: ocean-sst-analysis\ndescription: Example\n---\n\nCurrent body\n';
    fs.writeFileSync(skillFile, text);
    let lookups = 0;
    const controller = new AbortController();
    const agent = {};
    const ctx = { skills: {
      async list(lookup) { assert.equal(lookup.scope, agent); assert.equal(lookup.signal, controller.signal); },
      async get(name) { assert.equal(name, 'ocean-sst-analysis'); lookups++;
        return { path: skillFile, content: lookups === 1 ? 'Previous body' : 'Current body',
          invocation: { modelInvocable: true } }; },
    } };
    assert.equal(await waitForSkillBody(ctx, { projectRoot: root }, { agent, signal: controller.signal },
      'ocean-sst-analysis', skillFile, skillBodyHash(text)), true);
    assert.equal(lookups, 2);
    controller.abort();
    await assert.rejects(waitForSkillBody(ctx, { projectRoot: root }, { agent, signal: controller.signal },
      'ocean-sst-analysis', skillFile, skillBodyHash(text)), { name: 'AbortError' });
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

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
