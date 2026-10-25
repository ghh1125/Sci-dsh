import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

// Shared by all agents in this Harness process. A separate process has its own lock.
const dataOperations = new Map();
const DATA_ENV_NAMES = ['SCI_DSH_DATA_PROXY', 'SCI_DSH_DATA_CA_BUNDLE'];

export function currentDataOperation(root) {
  return dataOperations.get(fs.realpathSync(root))?.kind ?? null;
}

export async function withDataOperation(root, kind, operation) {
  const key = fs.realpathSync(root);
  const active = dataOperations.get(key);
  if (active) return { ok: false, code: 'DATA_BUSY', activeOperation: active.kind,
    error: { category: 'DATA_BUSY', message: '该项目已有数据准备或分析正在进行。', retryable: true,
      action: '等待当前操作结束后再请求；不要并发下载或分析。' }, nextTool: 'sst_status', artifacts: [] };
  const owner = { kind };
  dataOperations.set(key, owner);
  try { return await operation(); }
  finally { if (dataOperations.get(key) === owner) dataOperations.delete(key); }
}

/** Only the downloader receives explicit data-network overrides, never model arguments. */
export function pythonEnvironment(script, source = process.env) {
  const env = { PYTHONUNBUFFERED: '1', PYTHONUTF8: '1', MPLBACKEND: 'Agg' };
  if (script === 'fetch_noaa_oisst.py') for (const name of DATA_ENV_NAMES) {
    if (typeof source[name] === 'string' && source[name].trim()) env[name] = source[name];
  }
  return env;
}

function redactDataConfig(text) {
  const names = [...DATA_ENV_NAMES, 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'];
  let safe = String(text ?? '');
  for (const name of names) {
    const value = process.env[name];
    if (typeof value === 'string' && value.length > 0) {
      // JSON diagnostics may escape backslashes in a Windows CA-bundle path.
      for (const form of new Set([value, JSON.stringify(value).slice(1, -1)])) {
        safe = safe.split(form).join('[REDACTED_DATA_CONFIG]');
      }
    }
  }
  return safe;
}

export function simpleName(value, label = '名称') {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value)) {
    throw new Error(`${label}只能包含字母、数字、短横线和下划线，长度不超过64。`);
  }
  return value;
}

export function projectPath(root, ...parts) {
  const base = fs.realpathSync(root);
  let current = base;
  for (const part of parts) {
    if (!part || part === '.' || part === '..' || /[/\\]/.test(part)) throw new Error('路径片段无效。');
    current = path.join(current, part);
    if (fs.existsSync(current) && fs.lstatSync(current).isSymbolicLink()) throw new Error('不使用符号链接路径。');
  }
  return current;
}

export function evidence(config, row) {
  if (!config.evidencePath) return;
  fs.mkdirSync(path.dirname(config.evidencePath), { recursive: true });
  fs.appendFileSync(config.evidencePath, JSON.stringify({ at: new Date().toISOString(), ...row }) + '\n');
}

export function readJson(filename) {
  return JSON.parse(fs.readFileSync(filename, 'utf8'));
}

// The filesystem Skill provider removes frontmatter and trims its loaded body.
export function skillBodyHash(text) {
  const body = text.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '').trim();
  return createHash('sha256').update(body).digest('hex');
}

export async function waitForSkillBody(ctx, config, exec, name, skillFile, expectedHash) {
  const lookup = { cwd: config.projectRoot, signal: exec.signal, scope: exec.agent };
  const expectedPath = fs.realpathSync(skillFile);
  for (let i = 0; i < 30; i++) {
    exec.signal?.throwIfAborted();
    await ctx.skills.list(lookup);
    const definition = await ctx.skills.get(name, lookup);
    if (definition?.path && definition.invocation?.modelInvocable === true
      && fs.realpathSync(definition.path) === expectedPath
      && createHash('sha256').update(definition.content).digest('hex') === expectedHash) return true;
    if (i < 29) await delay(100, undefined, { signal: exec.signal });
  }
  return false;
}

export function successfulRun(root, run) {
  const output = projectPath(root, 'outputs', simpleName(run, '运行目录'));
  const report = readJson(projectPath(root, 'outputs', run, 'report.json'));
  if (report.status !== 'ok' || !Array.isArray(report.artifacts)) throw new Error('只接受有成功报告的运行目录。');
  return { output, report };
}

export async function runPython(ctx, config, exec, script, args, toolName) {
  const operationId = randomUUID();
  const logs = projectPath(config.projectRoot, 'outputs', 'tool-logs', operationId);
  fs.mkdirSync(logs, { recursive: true });
  evidence(config, { event: 'workflow-start', tool: toolName, operationId, callId: exec.callId ?? null,
    sessionId: exec.agent?.session?.id ?? null });
  let child = null, failure = null;
  let outcome = { exitCode: null, signal: null };
  let stdout = null, stderr = null;
  try {
    const executable = await ctx.subprocess.resolveExecutable(config.python, undefined, exec.signal);
    const argv = [executable, projectPath(config.projectRoot, 'scripts', script), ...args];
    child = ctx.subprocess.spawn({ argv, cwd: config.projectRoot, signal: exec.signal, graceMs: 1500,
      stdio: { stdin: 'ignore', stdout: { maxBytes: 1048576 }, stderr: { maxBytes: 262144 } },
      env: pythonEnvironment(script) });
    outcome = await child.done;
  } catch (error) { failure = error; }
  finally {
    try {
      if (child) {
        if (exec.signal?.aborted || failure) child.terminate();
        // Do not use the already-aborted caller signal for cleanup. The managed
        // subprocess owns its grace/escalation and must finish before the lock releases.
        await child.waitForExit();
      }
    } catch (error) { failure ??= error; }
    finally {
      // Collection remains readable after settlement, including cancellation and
      // wait failures. Save diagnostics before returning any failure to the agent.
      stdout = child?.collected.stdout?.readFrom(0) ?? null;
      stderr = child?.collected.stderr?.readFrom(0) ?? null;
    }
  }
  const stdoutText = redactDataConfig(stdout?.text);
  const stderrText = redactDataConfig(stderr?.text);
  fs.writeFileSync(path.join(logs, 'stdout.txt'), stdoutText);
  fs.writeFileSync(path.join(logs, 'stderr.txt'), stderrText);
  let result = null;
  try { result = JSON.parse(stdoutText); } catch {}
  const cancelled = Boolean(exec.signal?.aborted);
  const error = cancelled ? { category: 'CANCELLED', message: '本次操作已取消，已保留收集到的日志。',
    retryable: true, action: '需要继续时请重新请求准备数据；已有成功缓存可复用。' }
    : failure ? { category: 'PROCESS_ERROR', message: redactDataConfig(failure.message ?? failure),
      retryable: false, action: '查看本次日志并检查本地Python环境，再决定是否重试。' }
      : result?.error ?? null;
  const value = { ok: !cancelled && !failure && outcome.exitCode === 0 && result?.status !== 'error',
    exitCode: outcome.exitCode ?? null, signal: outcome.signal ?? null, cancelled, error,
    result, logs, operationId, stderr: stderrText.slice(-8000),
    stdout: result === null ? stdoutText.slice(-10000) : '',
    truncated: Boolean(stdout?.lossy || stderr?.lossy) };
  fs.writeFileSync(path.join(logs, 'operation.json'), JSON.stringify({ tool: toolName, operationId,
    ok: value.ok, exitCode: value.exitCode, signal: value.signal, cancelled, error }, null, 2));
  evidence(config, { event: 'workflow-end', tool: toolName, operationId, callId: exec.callId ?? null,
    sessionId: exec.agent?.session?.id ?? null, ok: value.ok, exitCode: value.exitCode,
    cancelled, errorCategory: error?.category ?? null });
  return value;
}
