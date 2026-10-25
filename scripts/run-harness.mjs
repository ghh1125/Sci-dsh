import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { projectRoot, findDshRuntime, resolvePython } from './runtime-paths.mjs';
import { verifyWorkflow } from '../harness/verify-workflow.mjs';

function options(argv) {
  const out = { case: 'A', style: null, 'set-style': null, output: null, prompt: null,
    baseline: [1991, 2020], web: false, check: false, port: '3080', timeout: 1800 };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if (key === '--web') out.web = true;
    else if (key === '--check') out.check = true;
    else if (key === '--baseline') out.baseline = [Number(argv[++i]), Number(argv[++i])];
    else if (['--case', '--style', '--set-style', '--output', '--prompt', '--port', '--timeout'].includes(key)) {
      if (!argv[i + 1]) throw new Error(`${key} 缺少参数。`);
      out[key.slice(2)] = argv[++i];
    } else if (key === '--help') {
      console.log('npm run web -- --port 3080\nnpm run dsh -- --prompt "准备海温数据"\nnpm run demo -- --case A --output run-A --baseline 1991 2020\nnpm run demo -- --set-style presentation --case A --output run-A-v2\nnpm run harness:check\n所有科研业务通过Harness工具执行。仅首次依赖安装和API配置在外部准备。');
      process.exit(0);
    } else throw new Error(`不支持的参数：${key}`);
  }
  if (!['A', 'B', 'missing-month'].includes(out.case)) throw new Error('case 必须为 A、B 或 missing-month。');
  for (const key of ['style', 'set-style']) if (out[key] !== null && !['default', 'presentation'].includes(out[key])) throw new Error(`${key} 无效。`);
  if (out.style !== null && out['set-style'] !== null) throw new Error('--style 和 --set-style 不能同时使用。');
  if (out.prompt && (out.style || out['set-style'])) throw new Error('使用 --prompt 时，在自然语言指令中指定样式。');
  if (!out.baseline.every(Number.isInteger) || out.baseline[1] - out.baseline[0] !== 29) throw new Error('基准期需要连续完整30年，例如 --baseline 1982 2011。');
  out.output ??= `run-${out.case}-${Date.now()}`;
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(out.output)) throw new Error('output 必须是简单子目录名。');
  out.timeout = Number(out.timeout);
  if (!Number.isInteger(out.timeout) || out.timeout < 1 || out.timeout > 3600) throw new Error('timeout 必须为1–3600秒。');
  if (!/^\d+$/.test(out.port) || +out.port < 1024 || +out.port > 65535) throw new Error('port 必须为1024–65535。');
  return out;
}

try {
  const opts = options(process.argv.slice(2));
  const envFile = path.join(projectRoot, '.env');
  if (fs.existsSync(envFile)) process.loadEnvFile(envFile);
  const runtime = findDshRuntime();
  const python = resolvePython();
  const py = spawnSync(python, ['-c', 'import sys,numpy,matplotlib; print(sys.version.split()[0]); print(numpy.__version__); print(matplotlib.__version__)'], { encoding: 'utf8' });
  if (py.status !== 0) throw new Error('Python依赖未准备好，请按README建立.venv并安装requirements.txt。');
  const apiKey = process.env.SCI_DSH_API_KEY?.trim();
  const model = process.env.SCI_DSH_MODEL?.trim();
  const baseURL = process.env.SCI_DSH_BASE_URL?.trim();
  if (baseURL) {
    const parsed = new URL(baseURL);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('SCI_DSH_BASE_URL必须是无凭据、无查询参数的HTTPS API入口。');
  }
  const installed = fs.existsSync(path.join(projectRoot, 'skills/ocean-sst-analysis/SKILL.md'));
  if (opts.check) {
    console.log(JSON.stringify({ harness: runtime.version, tools: runtime.toolsVersion,
      python: py.stdout.trim().split('\n'), templateExists: fs.existsSync(path.join(projectRoot, 'skill-templates/ocean-sst-analysis/SKILL.md')),
      skillInstalled: installed, credentialPresent: Boolean(apiKey), modelPresent: Boolean(model), baseURL: baseURL ?? null,
      note: '不调用模型API；首次没有已安装Skill是正常状态。' }, null, 2));
    process.exit(0);
  }
  if (!baseURL || !model || !apiKey) throw new Error('请在本地.env填写SCI_DSH_BASE_URL、SCI_DSH_MODEL、SCI_DSH_API_KEY；模型名及/v1路径需以服务商信息为准。');
  const id = new Date().toISOString().replace(/[:.]/g, '-');
  const evidence = path.join(projectRoot, 'outputs/harness-evidence', id);
  fs.mkdirSync(evidence, { recursive: true });
  const env = { ...process.env, DSH_HOME: path.join(projectRoot, '.runtime/dsh'),
    SCI_DSH_BASE_URL: baseURL, SCI_DSH_MODEL: model, SCI_DSH_API_KEY: apiKey,
    SCI_DSH_PROJECT_ROOT: projectRoot, SCI_DSH_PYTHON: python,
    SCI_DSH_PLUGIN: pathToFileURL(path.join(projectRoot, 'harness/ocean-tools.mjs')).href,
    SCI_DSH_TOOLS_MODULE: runtime.tools, SCI_DSH_EVIDENCE_PATH: path.join(evidence, 'tool-events.jsonl'),
    SCI_DSH_WEB_URL: opts.web ? `http://127.0.0.1:${opts.port}` : '', PYTHONUTF8: '1' };
  const style = opts.style === null ? '省略style，使用当前默认值' : `style=${opts.style}`;
  const update = opts['set-style'] === null ? '' : `用户要求修改默认样式：调用sst_set_style(style="${opts['set-style']}")并重新加载Skill。`;
  const preparation = installed ? '先调用skill加载ocean-sst-analysis。' : '首次尚未安装专用Skill，可以直接调用基础分析工具；不要假装已加载Skill。';
  const task = opts.prompt ?? `${preparation}${update}调用sst_analyze分析case=${opts.case}，${style}，output=${opts.output}，基准期${opts.baseline.join('–')}。成功后调用sst_show_figure展示该运行的图片；若缺月或其他校验失败，报告真实问题并停止，不补值、不换数据。`;
  const args = [runtime.cli, '--profile', opts.web ? 'web' : 'headless', '--patch', path.join(projectRoot, 'harness/sci.patch.yml')];
  if (opts.web) args.push('--patch', path.join(projectRoot, 'harness/sci.web.patch.yml'), '--host', '127.0.0.1', '--port', opts.port);
  else args.push(task);
  fs.writeFileSync(path.join(evidence, 'run.json'), JSON.stringify({ started: new Date().toISOString(),
    harnessVersion: runtime.version, mode: opts.web ? 'web' : 'headless', task: opts.web ? null : task,
    case: opts.case, output: opts.output, baseline: opts.baseline, style: opts.style, setStyle: opts['set-style'] }, null, 2));
  console.log(opts.web ? `DeepSeek Harness：http://127.0.0.1:${opts.port}；结果图库：/sci-results`
    : `DeepSeek Harness任务已启动；证据目录：${path.relative(projectRoot, evidence)}`);
  const stdout = fs.createWriteStream(path.join(evidence, 'stdout.txt'));
  const stderr = fs.createWriteStream(path.join(evidence, 'stderr.txt'));
  const child = spawn(process.execPath, ['--expose-internals', ...args], { cwd: projectRoot, env, stdio: ['inherit', 'pipe', 'pipe'] });
  // A provider's errors can quote its configuration. Redact a key even if split
  // across stream chunks by buffering complete lines before displaying/saving.
  function relay(stream, destination, screen) {
    let pending = '';
    stream.setEncoding('utf8');
    const flush = line => { const safe = line.split(apiKey).join('[REDACTED]'); destination.write(safe); screen.write(safe); };
    stream.on('data', chunk => {
      pending += chunk;
      const split = pending.lastIndexOf('\n');
      if (split >= 0) { flush(pending.slice(0, split + 1)); pending = pending.slice(split + 1); }
    });
    stream.on('end', () => { if (pending) flush(pending); });
  }
  relay(child.stdout, stdout, process.stdout);
  relay(child.stderr, stderr, process.stderr);
  let timedOut = false;
  const timer = opts.web ? null : setTimeout(() => { timedOut = true; child.kill('SIGTERM'); }, opts.timeout * 1000);
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('error', error => { console.error(error.message.split(apiKey).join('[REDACTED]')); });
  child.on('close', (code, signal) => {
    if (timer) clearTimeout(timer);
    stdout.end(); stderr.end();
    let events = [];
    try { events = fs.readFileSync(path.join(evidence, 'tool-events.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)); } catch {}
    const expected = opts.prompt || opts.web ? null : { case: opts.case,
      output: path.join(fs.realpathSync(projectRoot), 'outputs', opts.output), baseline: opts.baseline,
      style: opts.style ?? opts['set-style'], setStyle: opts['set-style'] };
    const verification = verifyWorkflow(events, expected);
    fs.writeFileSync(path.join(evidence, 'verification.json'), JSON.stringify({ finished: new Date().toISOString(),
      code, signal, timedOut, ...verification }, null, 2));
    if (!opts.web && code === 0 && !verification.ok) {
      console.error(`工具流程未通过证据检查：${verification.failures.join('；')}`);
      process.exitCode = 2;
    } else process.exitCode = timedOut ? 124 : (code ?? 1);
  });
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
