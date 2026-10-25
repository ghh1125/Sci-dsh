import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { figureLinks, mountGallery } from '../harness/gallery.mjs';

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sci-gallery-'));
  const directory = path.join(root, 'outputs', 'run-A');
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'sst_analysis.png'), Buffer.from([137, 80, 78, 71]));
  fs.writeFileSync(path.join(directory, 'summary.md'), 'Explanation <script>alert(1)</script>');
  const report = { status: 'ok', point: { lat: 15.125, lon: 114.125 }, baseline: [1991, 2020],
    coverage: ['1982-01', '2025-12'], style: 'presentation',
    source: { source_title: 'NOAA <example>', source_url: 'javascript:alert(1)' },
    artifacts: ['sst_analysis.png', 'summary.md', 'report.json'] };
  const reportPath = path.join(directory, 'report.json');
  fs.writeFileSync(reportPath, JSON.stringify(report));
  return { root, directory, report, reportPath, remove: () => fs.rmSync(root, { recursive: true, force: true }) };
}

function handlerFor(root) {
  let route;
  const webCtx = { webServer: { host: '127.0.0.1', port: 3080,
    register(value) { route = value; return () => {}; } }, effect(callback) { return callback(); } };
  mountGallery({ inject(names, callback) { assert.deepEqual(names, ['webServer']); callback(webCtx); } },
    { projectRoot: root, baseURL: 'http://127.0.0.1:3080' });
  assert.equal(route.path, '/sci-results');
  return (url, overrides = {}) => {
    const response = { status: null, headers: null, body: null,
      writeHead(status, headers) { this.status = status; this.headers = headers; },
      end(body) { this.body = body; } };
    route.handler({ method: 'GET', url, headers: { host: '127.0.0.1:3080' }, socket: { remoteAddress: '127.0.0.1' }, ...overrides }, response);
    return response;
  };
}

test('figure links expose only declared successful files and do not claim a headless server', () => {
  const f = fixture();
  try {
    const local = figureLinks(f.root, 'run-A');
    assert.equal(local.galleryURL, null);
    assert.equal(local.webConfigured, false);
    assert.ok(local.artifactPaths.every(file => file.startsWith(fs.realpathSync(f.root))));
    const web = figureLinks(f.root, 'run-A', 'http://127.0.0.1:3080');
    assert.equal(web.galleryURL, 'http://127.0.0.1:3080/sci-results/run-A');
    assert.throws(() => figureLinks(f.root, '../run-A'));
    assert.throws(() => figureLinks(f.root, 'run-A', 'http://example.com:3080'));
    f.report.status = 'error';
    fs.writeFileSync(f.reportPath, JSON.stringify(f.report));
    assert.throws(() => figureLinks(f.root, 'run-A'), /成功/);
  } finally { f.remove(); }
});

test('gallery escapes text, enforces host and GET/HEAD, and refuses undeclared files', () => {
  const f = fixture();
  try {
    const request = handlerFor(f.root);
    const page = request('/sci-results/run-A');
    assert.equal(page.status, 200);
    assert.match(page.body.toString(), /&lt;script&gt;/);
    assert.doesNotMatch(page.body.toString(), /href="javascript:/);
    assert.equal(request('/sci-results/run-A/report.json').headers['Content-Disposition'], 'attachment; filename="report.json"');
    const head = request('/sci-results/run-A/sst_analysis.png', { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(head.body, undefined);
    assert.equal(request('/sci-results/run-A', { method: 'POST' }).status, 405);
    assert.equal(request('/sci-results/run-A', { headers: { host: 'evil.test:3080' } }).status, 403);
    assert.equal(request('/sci-results/run-A', { headers: { host: '127.0.0.1:3080', 'sec-fetch-site': 'cross-site' } }).status, 403);
    assert.equal(request('/sci-results/run-A/.env').status, 404);
    assert.equal(request('/sci-results/%2e%2e/report.json').status, 404);
    assert.equal(request('/sci-results/run-A/%2e%2fsummary.md').status, 404);
    assert.equal(request('/sci-results/run-A/%ZZ').status, 400);
  } finally { f.remove(); }
});

test('gallery refuses symlink artifacts, symlink runs, and traversal in report declarations', () => {
  const f = fixture();
  try {
    f.report.artifacts.push('../outside.csv');
    fs.writeFileSync(f.reportPath, JSON.stringify(f.report));
    assert.throws(() => figureLinks(f.root, 'run-A'), /未允许/);
    f.report.artifacts.pop();
    fs.writeFileSync(f.reportPath, JSON.stringify(f.report));
    fs.unlinkSync(path.join(f.directory, 'summary.md'));
    fs.symlinkSync(f.reportPath, path.join(f.directory, 'summary.md'));
    assert.throws(() => figureLinks(f.root, 'run-A'), /普通文件/);
    fs.symlinkSync(f.directory, path.join(f.root, 'outputs', 'linked-run'), 'junction');
    assert.throws(() => figureLinks(f.root, 'linked-run'), /符号链接/);
  } finally { f.remove(); }
});
