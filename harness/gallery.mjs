import fs from 'node:fs';
import path from 'node:path';

const RUN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const PREFIX = '/sci-results';
const TYPES = Object.freeze({
  'sst_analysis.png': 'image/png',
  'sst_analysis.svg': 'image/svg+xml',
  'sst_analysis.pdf': 'application/pdf',
  'result.csv': 'text/csv; charset=utf-8',
  'climatology.csv': 'text/csv; charset=utf-8',
  'summary.md': 'text/plain; charset=utf-8',
  'report.json': 'application/json; charset=utf-8',
});
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function originOf(baseURL) {
  if (!baseURL) return null;
  const url = new URL(baseURL);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password
      || url.pathname !== '/' || url.search || url.hash || !url.port) {
    throw new Error('图库地址必须为 http://127.0.0.1:<port>，且不含路径、查询或凭据。');
  }
  return url.origin;
}

function regularFile(filename, limit = 64 * 1024 * 1024) {
  const stat = fs.lstatSync(filename);
  if (stat.isSymbolicLink() || !stat.isFile() || stat.size > limit) throw new Error('产物不是允许大小内的普通文件。');
  return stat;
}

function readFile(filename, limit) {
  regularFile(filename, limit);
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0));
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > limit) throw new Error('产物不是允许大小内的普通文件。');
    return fs.readFileSync(fd);
  } finally { fs.closeSync(fd); }
}

function readRun(root, run) {
  if (typeof run !== 'string' || !RUN.test(run)) throw new Error('run 必须是 outputs 下的简单子目录名。');
  const absoluteRoot = fs.realpathSync(root);
  const outputs = path.join(absoluteRoot, 'outputs');
  const directory = path.join(outputs, run);
  for (const candidate of [outputs, directory]) {
    const stat = fs.lstatSync(candidate);
    if (stat.isSymbolicLink() || !stat.isDirectory() || fs.realpathSync(candidate) !== candidate) {
      throw new Error('结果目录不能经过符号链接。');
    }
  }
  const report = JSON.parse(readFile(path.join(directory, 'report.json'), 4 * 1024 * 1024).toString('utf8'));
  if (report?.status !== 'ok' || !Array.isArray(report.artifacts)
      || !report.artifacts.includes('sst_analysis.png') || !report.artifacts.includes('report.json')) {
    throw new Error('只有 report.json 明确成功并声明图像产物的结果可以展示。');
  }
  if (new Set(report.artifacts).size !== report.artifacts.length) throw new Error('产物声明重复。');
  for (const filename of report.artifacts) {
    if (typeof filename !== 'string' || !Object.hasOwn(TYPES, filename)) throw new Error('report.json 声明了未允许的产物。');
    regularFile(path.join(directory, filename));
  }
  return { directory, report };
}

/** Return validated local products, and optional Web links; this does not start or probe a server. */
export function figureLinks(root, run, baseURL) {
  const { directory, report } = readRun(root, run);
  const origin = originOf(baseURL);
  const galleryURL = origin ? `${origin}${PREFIX}/${encodeURIComponent(run)}` : null;
  const artifacts = report.artifacts.map(name => ({ name, path: path.join(directory, name),
    mediaType: TYPES[name], url: galleryURL ? `${galleryURL}/${encodeURIComponent(name)}` : null }));
  return {
    ok: true, run, galleryURL, webConfigured: Boolean(origin),
    notice: origin ? '此链接用于当前 DeepSeek Harness Web；链接生成不代表服务连通性已验证。'
      : '当前未配置 Web 图库。可直接打开本地 PNG；如需浏览器预览，请启动 npm run web 后再次调用本工具。',
    artifactPaths: artifacts.map(artifact => artifact.path), artifacts,
    point: report.point ?? null, coverage: report.coverage ?? null,
    baseline: report.baseline ?? null, style: report.style ?? null,
    source: { title: report.source?.source_title ?? report.source?.product ?? '',
      product: report.source?.product ?? '', url: report.source?.source_url ?? '' },
    spot_check: report.spot_check ?? null,
  };
}

function safeSourceURL(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function galleryHTML(links, directory) {
  const sourceURL = safeSourceURL(links.source.url);
  const source = sourceURL ? `<a href="${escape(sourceURL)}" target="_blank" rel="noopener noreferrer">${escape(links.source.title)}</a>` : escape(links.source.title);
  const summary = links.artifacts.some(file => file.name === 'summary.md')
    ? readFile(path.join(directory, 'summary.md'), 1024 * 1024).toString('utf8') : '本次结果未声明文字说明。';
  const artifactLinks = links.artifacts.map(file => `<a href="${PREFIX}/${encodeURIComponent(links.run)}/${encodeURIComponent(file.name)}?download=1" download="${escape(file.name)}">${escape(file.name)}</a>`).join('');
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>海温结果 · ${escape(links.run)}</title>
<style>body{font:16px/1.65 system-ui,sans-serif;color:#16384a;background:#f3f7fa;margin:0}main{max-width:1120px;margin:auto;padding:28px}h1{line-height:1.25}article{background:white;border-radius:12px;padding:24px;margin:20px 0}img{display:block;max-width:100%;height:auto;margin:auto}dl{display:grid;grid-template-columns:100px 1fr;gap:6px 12px}dt{font-weight:650}dd{margin:0;overflow-wrap:anywhere}nav{display:flex;flex-wrap:wrap;gap:12px}a{color:#006994}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.note{color:#526976}</style>
<main><h1>海温分析结果</h1><p class="note">运行：${escape(links.run)}。仅展示成功报告声明的本地文件。</p>
<article><dl><dt>格点坐标</dt><dd>纬度 ${escape(links.point?.lat)}；经度 ${escape(links.point?.lon)}</dd><dt>数据覆盖</dt><dd>${escape(links.coverage?.join(' 至 '))}</dd><dt>基准期</dt><dd>${escape(links.baseline?.join('–'))}</dd><dt>绘图样式</dt><dd>${escape(links.style)}</dd><dt>数据来源</dt><dd>${source}</dd></dl></article>
<article><img src="${PREFIX}/${encodeURIComponent(links.run)}/sst_analysis.png" alt="该格点月平均海温、同月气候态和海温异常"><p class="note">单格点分析不代表整个南海区域平均；本示例未进行趋势显著性检验、海洋热浪判定或因果归因。</p><nav aria-label="下载结果">${artifactLinks}</nav></article>
<article><h2>结果说明</h2><pre>${escape(summary)}</pre></article></main></html>`;
}

/** Register a loopback-only, read-only route when the optional DSH webServer service exists. */
export function mountGallery(ctx, { projectRoot, baseURL }) {
  const origin = originOf(baseURL);
  if (!origin) return;
  ctx.inject(['webServer'], webCtx => {
    if (webCtx.webServer.host !== '127.0.0.1' || origin !== `http://127.0.0.1:${webCtx.webServer.port}`) {
      throw new Error('图库配置必须与当前仅回环监听的 Web 服务地址一致。');
    }
    webCtx.effect(() => webCtx.webServer.register({ kind: 'prefix', path: PREFIX,
      handler(req, res) {
        const send = (status, body, type = 'text/plain; charset=utf-8', extra = {}) => {
          const bytes = Buffer.isBuffer(body) ? body : Buffer.from(body);
          res.writeHead(status, { 'Content-Type': type, 'Content-Length': bytes.length,
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'", ...extra });
          res.end(req.method === 'HEAD' ? undefined : bytes);
        };
        if (!['GET', 'HEAD'].includes(req.method)) return send(405, '只支持 GET 和 HEAD。', undefined, { Allow: 'GET, HEAD' });
        if (req.headers.host !== new URL(origin).host || (req.headers.origin && req.headers.origin !== origin)
            || req.headers['sec-fetch-site'] === 'cross-site') return send(403, '请求来源不匹配。');
        const remote = req.socket?.remoteAddress;
        if (remote && !['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(remote)) return send(403, '仅支持本机访问。');
        try {
          const rawPath = (req.url ?? '').split('?')[0];
          const parts = rawPath.split('/').map(part => decodeURIComponent(part));
          if (parts[0] !== '' || parts[1] !== 'sci-results' || parts.length < 3 || parts.length > 4 || !RUN.test(parts[2])) {
            return send(404, '请使用工具返回的具体结果链接。');
          }
          const run = parts[2];
          const { directory } = readRun(projectRoot, run);
          const links = figureLinks(projectRoot, run, origin);
          if (parts.length === 3) return send(200, galleryHTML(links, directory), 'text/html; charset=utf-8');
          const name = parts[3];
          const artifact = links.artifacts.find(file => file.name === name);
          if (!artifact) return send(404, '该文件不属于本次成功结果。');
          const download = new URL(req.url, origin).searchParams.get('download') === '1' || name !== 'sst_analysis.png';
          const bytes = readFile(artifact.path, 64 * 1024 * 1024);
          return send(200, bytes, artifact.mediaType, download ? { 'Content-Disposition': `attachment; filename="${name}"` } : {});
        } catch (error) {
          // Keep filesystem paths and report contents out of rejection responses.
          return send(error instanceof URIError ? 400 : 404, '结果不可展示：不存在、未成功、产物不完整或路径不被允许。');
        }
      },
    }), 'scientific result gallery');
  });
}
