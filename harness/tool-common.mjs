import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

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
  const executable = await ctx.subprocess.resolveExecutable(config.python, undefined, exec.signal);
  const argv = [executable, projectPath(config.projectRoot, 'scripts', script), ...args];
  evidence(config, { event: 'workflow-start', tool: toolName, operationId, callId: exec.callId ?? null });
  const child = ctx.subprocess.spawn({ argv, cwd: config.projectRoot, signal: exec.signal, graceMs: 1500,
    stdio: { stdin: 'ignore', stdout: { maxBytes: 1048576 }, stderr: { maxBytes: 262144 } },
    env: { PYTHONUNBUFFERED: '1', PYTHONUTF8: '1', MPLBACKEND: 'Agg' } });
  const outcome = await child.done;
  await child.waitForExit(exec.signal);
  const stdout = child.collected.stdout?.readFrom(0);
  const stderr = child.collected.stderr?.readFrom(0);
  fs.writeFileSync(path.join(logs, 'stdout.txt'), stdout?.text ?? '');
  fs.writeFileSync(path.join(logs, 'stderr.txt'), stderr?.text ?? '');
  let result = null;
  try { result = JSON.parse(stdout?.text ?? ''); } catch {}
  const value = { ok: outcome.exitCode === 0, exitCode: outcome.exitCode, signal: outcome.signal,
    result, logs, operationId, stderr: (stderr?.text ?? '').slice(-8000),
    stdout: result === null ? (stdout?.text ?? '').slice(-10000) : '',
    truncated: Boolean(stdout?.lossy || stderr?.lossy) };
  evidence(config, { event: 'workflow-end', tool: toolName, operationId, callId: exec.callId ?? null,
    ok: value.ok, exitCode: value.exitCode });
  return value;
}
