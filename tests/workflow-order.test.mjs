import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyWorkflow } from '../harness/verify-workflow.mjs';

const hash = 'a'.repeat(64), newHash = 'b'.repeat(64);
const start = { event: 'analysis-start', case: 'A', output: '/demo/run', baseline: [1991, 2020],
  skillRequired: true, skillBodySha256: hash, callId: 'analysis-1', sessionId: 'session-1' };
const end = { event: 'analysis-end', case: 'A', output: '/demo/run', ok: true, exitCode: 0,
  callId: 'analysis-1', sessionId: 'session-1' };
const loaded = { event: 'tool-result', tool: 'skill', skillName: 'ocean-sst-analysis', isError: false,
  bodySha256: hash, callId: 'load-1', sessionId: 'session-1' };
const shown = { event: 'tool-result', tool: 'sst_show_figure', run: 'run', isError: false,
  businessOK: true, callId: 'show-1', sessionId: 'session-1' };
const expected = { case: 'A', output: '/demo/run', baseline: [1991, 2020] };

test('loading after analysis cannot prove Skill use', () => {
  assert.equal(verifyWorkflow([start, end, loaded]).ok, false);
  assert.equal(verifyWorkflow([loaded, start, end]).ok, true);
});
test('changed Skill must be reloaded before reuse', () => {
  const changed = { event: 'style-updated', after: 'presentation', bodySha256: newHash, sessionId: 'session-1' };
  assert.equal(verifyWorkflow([loaded, changed, start, end, loaded]).ok, false);
  const current = { ...start, skillBodySha256: newHash };
  assert.equal(verifyWorkflow([loaded, changed, loaded, current, end]).ok, false);
  assert.equal(verifyWorkflow([loaded, changed, { ...loaded, bodySha256: newHash }, current, end]).ok, true);
});
test('first analysis can use base tools before a Skill is created', () => {
  assert.equal(verifyWorkflow([{ ...start, skillRequired: false }, end]).ok, true);
});
test('only the actual missing-month error completes the negative example', () => {
  const bad = { ...start, case: 'missing-month', skillRequired: false };
  const failedResult = { event: 'tool-result', tool: 'sst_analyze', isError: false, businessOK: false,
    callId: 'analysis-1', sessionId: 'session-1' };
  assert.equal(verifyWorkflow([bad, { ...end, ok: false, exitCode: 2, issues: ['缺少月份：2000-02'] }, failedResult]).ok, true);
  assert.equal(verifyWorkflow([bad, { ...end, ok: false, exitCode: 2, issues: ['密钥错误'] }]).ok, false);
});

test('another Web session cannot supply a Skill load or a matching analysis end', () => {
  assert.equal(verifyWorkflow([{ ...loaded, sessionId: 'other' }, start, end]).ok, false);
  assert.equal(verifyWorkflow([loaded, start, { ...end, sessionId: 'other' }]).ok, false);
  assert.equal(verifyWorkflow([loaded, start, { ...end, callId: 'different-call' }]).ok, false);
});

test('the canonical business outcome overrides a zero-exit process event', () => {
  const processStart = { event: 'workflow-start', operationId: 'op-1', tool: 'sst_prepare_data' };
  const processEnd = { event: 'workflow-end', operationId: 'op-1', tool: 'sst_prepare_data', ok: true };
  assert.equal(verifyWorkflow([processStart, processEnd,
    { event: 'tool-result', tool: 'sst_prepare_data', isError: false, businessOK: false }]).ok, false);
  assert.equal(verifyWorkflow([{ event: 'tool-result', tool: 'sst_install_skill', isError: false,
    businessOK: true, ready: false }]).ok, false);
});

test('a preset successful analysis must return the matching figure afterward', () => {
  assert.equal(verifyWorkflow([loaded, start, end], expected).ok, false);
  assert.equal(verifyWorkflow([loaded, shown, start, end], expected).ok, false);
  assert.equal(verifyWorkflow([loaded, start, end, { ...shown, run: 'different' }], expected).ok, false);
  assert.equal(verifyWorkflow([loaded, start, end, { ...shown, sessionId: 'other' }], expected).ok, false);
  assert.equal(verifyWorkflow([loaded, start, end, shown], expected).ok, true);
});

test('a later style mutation cannot satisfy a preset change request retroactively', () => {
  const styled = { ...start, style: 'presentation' };
  const change = { event: 'style-updated', after: 'presentation', sessionId: 'session-1' };
  const target = { ...expected, style: 'presentation', setStyle: 'presentation' };
  assert.equal(verifyWorkflow([loaded, styled, end, shown, change], target).ok, false);
  assert.equal(verifyWorkflow([change, loaded, styled, end, shown], target).ok, true);
});
