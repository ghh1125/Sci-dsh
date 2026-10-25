import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { projectRoot, findDshRuntime, resolvePython } from './runtime-paths.mjs';

function options(argv) {
  const out = { case: 'A', style: null, 'set-style': null, output: null, web: false, check: false, port: '3080', timeout: 600 };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if (key === '--web') out.web = true;
    else if (key === '--check') out.check = true;
    else if (['--case', '--style', '--set-style', '--output', '--port', '--timeout'].includes(key)) {
      if (!argv[i + 1]) throw new Error(`${key} 缺少参数。`);
      out[key.slice(2)] = argv[++i];
    } else if (key === '--help') {
      console.log('npm run demo -- --case A|B|missing-month --style default|presentation --output run-A\nnpm run demo -- --set-style presentation --case A --output run-A-presentation\nnpm run web -- --port 3080\nnpm run harness:check\n自动读取项目 .env；需要 SCI_DSH_BASE_URL、SCI_DSH_MODEL 和 SCI_DSH_API_KEY。\n可用 SCI_DSH_PYTHON 或 SST_PYTHON 指定已安装 numpy/matplotlib 的 Python。省略 --style 时使用 Skill 默认样式。\n--style 仅覆盖本次绘图；--set-style 修改 Skill 默认值、重载并分析，不能同时指定。');
      process.exit(0);
    } else throw new Error(`不支持的参数：${key}`);
  }
  if (!['A', 'B', 'missing-month'].includes(out.case)) throw new Error('case 必须是 A、B 或 missing-month。');
  if (out.style !== null && !['default', 'presentation'].includes(out.style)) throw new Error('style 必须是 default 或 presentation。');
  if (out['set-style'] !== null && !['default', 'presentation'].includes(out['set-style'])) throw new Error('set-style 必须是 default 或 presentation。');
  if (out.style !== null && out['set-style'] !== null) throw new Error('--style 与 --set-style 不能同时指定。');
  out.output ??= `run-${out.case}`;
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(out.output)) throw new Error('output 必须是 outputs 下的一个简单子目录名。');
  out.timeout = Number(out.timeout);
  if (!Number.isInteger(out.timeout) || out.timeout < 1 || out.timeout > 3600) throw new Error('timeout 必须是 1–3600 秒。');
  if (!/^\d+$/.test(out.port) || Number(out.port) < 1024 || Number(out.port) > 65535) throw new Error('port 必须介于 1024 和 65535。');
  return out;
}

try {
  const opts = options(process.argv.slice(2));
  const envFile = path.join(projectRoot, '.env');
  if (fs.existsSync(envFile)) process.loadEnvFile(envFile);
  const runtime = findDshRuntime();
  const python = resolvePython();
  const py = spawnSync(python, ['-c', 'import sys,numpy,matplotlib; print(sys.version.split()[0]); print(numpy.__version__); print(matplotlib.__version__)'], { encoding: 'utf8' });
  if (py.status !== 0) throw new Error('Python 或 numpy/matplotlib 未准备好。请建立 .venv 并安装 requirements.txt，或设置 SCI_DSH_PYTHON。');
  const credentialPresent = Boolean(process.env.SCI_DSH_API_KEY?.trim());
  const model = process.env.SCI_DSH_MODEL?.trim();
  const baseURL = process.env.SCI_DSH_BASE_URL?.trim();
  if (baseURL) {
    const parsedBase = new URL(baseURL);
    if (parsedBase.protocol !== 'https:' || parsedBase.username || parsedBase.password || parsedBase.search || parsedBase.hash) throw new Error('SCI_DSH_BASE_URL 必须是无凭据、无查询参数的 HTTPS API base URL。');
  }
  if (opts.check) {
    console.log(JSON.stringify({ harness: runtime.version, tools: runtime.toolsVersion, python: py.stdout.trim().split('\n'), skillExists: fs.existsSync(path.join(projectRoot, 'skills/ocean-sst-analysis/SKILL.md')), credentialEnvironmentPresent: credentialPresent, modelEnvironmentPresent: Boolean(model), baseURL: baseURL ?? null }, null, 2));
    process.exit(0);
  }
  if (!baseURL) throw new Error('必须通过 SCI_DSH_BASE_URL 指定代理的完整 API base URL；.env.example 中 /v1 仅是未验证的示例。');
  if (!model) throw new Error('必须通过 SCI_DSH_MODEL 指定代理实际支持的完整模型名；本项目不猜测模型。');
  if (!credentialPresent) throw new Error('请在启动环境设置 SCI_DSH_API_KEY；不要把密钥写进项目。');
  const runId = new Date().toISOString().replace(/[:.]/g, '-');
  const evidence = path.join(projectRoot, 'outputs', 'harness-evidence', runId);
  fs.mkdirSync(evidence, { recursive: true });
  const harnessHome = path.join(projectRoot, '.runtime', 'dsh');
  const env = { ...process.env,
    DSH_HOME: harnessHome,
    SCI_DSH_BASE_URL: baseURL,
    SCI_DSH_MODEL: model,
    SCI_DSH_API_KEY: process.env.SCI_DSH_API_KEY.trim(),
    SCI_DSH_PROJECT_ROOT: projectRoot,
    SCI_DSH_PYTHON: python,
    SCI_DSH_PLUGIN: path.join(projectRoot, 'harness/ocean-tools.mjs'),
    SCI_DSH_TOOLS_MODULE: runtime.tools,
    SCI_DSH_EVIDENCE_PATH: path.join(evidence, 'tool-events.jsonl'),
  };
  const styleRequest = opts.style === null ? '省略 style 参数，使用 Skill 的默认演示样式' : `style=${opts.style}`;
  const updateRequest = opts['set-style'] === null ? '' : `用户明确要求修改 Skill 默认样式：调用 sst_set_style(style="${opts['set-style']}")，再调用 skill 工具重新加载 ocean-sst-analysis。`;
  const task = `请先调用 skill 工具加载 ocean-sst-analysis。${updateRequest}然后用 sst_analyze 分析 case=${opts.case}，${styleRequest}，output=${opts.output}，基准期1991–2020。只根据本次工具返回的真实结果给出中文说明、关键数字和产物路径；如果检查失败，报告问题并停止，不修补、不重跑其他数据。`;
  const args = [runtime.cli, '--profile', opts.web ? 'web' : 'headless', '--patch', path.join(projectRoot, 'harness/sci.patch.yml')];
  if (opts.web) args.push('--patch', path.join(projectRoot, 'harness/sci.web.patch.yml'), '--host', '127.0.0.1', '--port', opts.port);
  else args.push(task);
  fs.writeFileSync(path.join(evidence, 'run.json'), JSON.stringify({ started: new Date().toISOString(), harnessVersion: runtime.version, toolsVersion: runtime.toolsVersion, mode: opts.web ? 'web' : 'headless', case: opts.case, style: opts.style, setStyle: opts['set-style'], output: opts.output, task: opts.web ? null : task }, null, 2));
  console.log(opts.web ? `启动 DeepSeek Harness：http://127.0.0.1:${opts.port}` : `DeepSeek Harness 正在处理数据 ${opts.case}；运行证据：${path.relative(projectRoot, evidence)}`);
  const stdoutFile = fs.createWriteStream(path.join(evidence, 'stdout.txt'));
  const stderrFile = fs.createWriteStream(path.join(evidence, 'stderr.txt'));
  const child = spawn(process.execPath, args, { cwd: projectRoot, env, stdio: ['inherit', 'pipe', 'pipe'] });
  // Only product output is relayed. We never print environment or configuration.
  child.stdout.on('data', chunk => { stdoutFile.write(chunk); process.stdout.write(chunk); });
  child.stderr.on('data', chunk => { stderrFile.write(chunk); process.stderr.write(chunk); });
  let timedOut = false;
  const timer = opts.web ? null : setTimeout(() => { timedOut = true; child.kill('SIGTERM'); }, opts.timeout * 1000);
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('error', error => { stderrFile.write(error.message); console.error(error.message); });
  child.on('close', (code, signal) => {
    if (timer) clearTimeout(timer);
    stdoutFile.end(); stderrFile.end();
    const eventsPath = path.join(evidence, 'tool-events.jsonl');
    let events = [];
    try { events = fs.readFileSync(eventsPath, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)); } catch {}
    const styleEventIndex = events.findLastIndex(e => e.event === 'style-updated' && e.after === opts['set-style']);
    const expectedOutput = path.join(fs.realpathSync(projectRoot), 'outputs', opts.output);
    const matchesTask = e => e.case === opts.case && e.output === expectedOutput
      && e.baseline?.[0] === 1991 && e.baseline?.[1] === 2020
      && (opts.style === null || e.style === opts.style)
      && (opts['set-style'] === null || e.style === opts['set-style']);
    const analysisEvents = events.filter(e => matchesTask(e));
    const result = { finished: new Date().toISOString(), code, signal, timedOut,
      skillLoaded: events.some(e => e.tool === 'skill' && e.skillName === 'ocean-sst-analysis' && !e.isError),
      styleUpdatedAndReloaded: styleEventIndex >= 0 && events.slice(styleEventIndex + 1).some(e => e.tool === 'skill' && e.skillName === 'ocean-sst-analysis' && !e.isError),
      analysisCalled: analysisEvents.some(e => e.event === 'analysis-start'),
      analysisSucceeded: analysisEvents.some(e => e.event === 'analysis-end' && e.ok),
      missingMonthDetected: analysisEvents.some(e => e.event === 'analysis-end' && e.exitCode === 2
        && e.issues?.some(issue => typeof issue === 'string' && issue.includes('2000-02') && issue.includes('缺少月份'))),
      evidence: path.relative(projectRoot, evidence) };
    fs.writeFileSync(path.join(evidence, 'verification.json'), JSON.stringify(result, null, 2));
    if (!opts.web && code === 0 && (!result.skillLoaded || !result.analysisCalled)) {
      console.error('本次模型未完成要求的 Skill 加载和工具调用，已保存证据，不能视为成功。');
      process.exitCode = 2;
    } else if (!opts.web && code === 0 && opts['set-style'] !== null && !result.styleUpdatedAndReloaded) {
      console.error('本次未验证到 Skill 样式修改后的重新加载，不能视为修改演示成功。');
      process.exitCode = 2;
    } else if (!opts.web && code === 0 && opts.case !== 'missing-month' && !result.analysisSucceeded) {
      console.error('本次分析未成功，不能把助手完成回复当作科研产物生成成功。');
      process.exitCode = 2;
    } else if (!opts.web && code === 0 && opts.case === 'missing-month' && !result.missingMonthDetected) {
      console.error('本次没有验证到预期的缺月错误，不能视为异常演练成功。');
      process.exitCode = 2;
    } else process.exitCode = timedOut ? 124 : (code ?? 1);
  });
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
