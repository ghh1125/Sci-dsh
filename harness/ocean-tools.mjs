import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const moduleName = process.env.SCI_DSH_TOOLS_MODULE;
const { defineTool } = await import(moduleName ? pathToFileURL(moduleName).href : '@deepseek-ai/dsh-tools');

export const name = 'sci-ocean-tools';
export const inject = ['tools', 'subprocess'];

const CASE_FILES = Object.freeze({
  A: ['point_A_monthly.csv', 'point_A.metadata.json'],
  B: ['point_B_monthly.csv', 'point_B.metadata.json'],
  'missing-month': ['point_B_gap_demo.csv', 'point_B_gap_demo.metadata.json'],
});

const STYLE_LINE = /^默认演示样式：(default|presentation)[ \t]*$/gm;

export function readDefaultStyle(root) {
  const text = fs.readFileSync(path.join(root, 'skills/ocean-sst-analysis/SKILL.md'), 'utf8');
  const matches = [...text.matchAll(STYLE_LINE)];
  if (matches.length !== 1) throw new Error('Skill 必须包含且仅包含一行：默认演示样式：default 或 presentation。');
  return matches[0][1];
}

export function buildInvocation(root, python, args) {
  if (!Object.hasOwn(CASE_FILES, args.case)) throw new Error('case 必须是 A、B 或 missing-month。');
  const style = args.style ?? readDefaultStyle(root);
  if (!['default', 'presentation'].includes(style)) throw new Error('不支持该绘图样式。');
  const first = args.baseline_start ?? 1991;
  const last = args.baseline_end ?? 2020;
  if (!Number.isInteger(first) || !Number.isInteger(last) || last - first !== 29 || first < 1800 || last > 2200) throw new Error('基准期必须是完整的连续 30 年。');
  const folder = args.output ?? `run-${args.case}`;
  // Only a simple child directory is accepted: no arbitrary paths or shell text.
  if (typeof folder !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(folder)) throw new Error('output 只能是 outputs 下的子目录名，由字母、数字、短横线和下划线组成。');
  const absoluteRoot = fs.realpathSync(root);
  const outputs = path.join(absoluteRoot, 'outputs');
  if (fs.existsSync(outputs) && fs.lstatSync(outputs).isSymbolicLink()) throw new Error('outputs 不能是符号链接。');
  const output = path.join(outputs, folder);
  if (fs.existsSync(output) && fs.lstatSync(output).isSymbolicLink()) throw new Error('输出子目录不能是符号链接。');
  if (fs.existsSync(output) && (!fs.statSync(output).isDirectory() || fs.readdirSync(output).length !== 0)) {
    throw new Error('输出目录已存在且非空。请指定新 output；本次没有运行，也不会列出或改写旧结果。');
  }
  const [input, metadata] = CASE_FILES[args.case];
  return {
    argv: [python, path.join(absoluteRoot, 'skills/ocean-sst-analysis/scripts/analyze_sst.py'),
      '--input', path.join(absoluteRoot, 'data', input), '--metadata', path.join(absoluteRoot, 'data', metadata),
      '--baseline', String(first), String(last), '--output', output, '--style', style],
    cwd: absoluteRoot, output, case: args.case, style, baseline: [first, last],
  };
}

function appendEvidence(filename, row) {
  if (!filename) return;
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  fs.appendFileSync(filename, JSON.stringify({ at: new Date().toISOString(), ...row }) + '\n');
}

export function makeAnalysisTool(ctx, config) {
  return defineTool({
    name: 'sst_analyze',
    description: '运行项目内固定的月海温分析脚本。先加载 ocean-sst-analysis Skill。只支持随项目提供的 A、B 和 missing-month 三组数据；返回真实退出状态、统计摘要和产物。不能自行修补缺月或更改算法。',
    parameters: {
      case: { type: 'string', required: true, enum: ['A', 'B', 'missing-month'], description: '选择演示数据。' },
      style: { type: 'string', enum: ['default', 'presentation'], description: '本次绘图样式；省略时读取 Skill 的“默认演示样式”行，不改变算法。' },
      output: { type: 'string', description: 'outputs 下的一个简单子目录名，例如 run-A。' },
      baseline_start: { type: 'integer', description: '30 年基准期首年，默认 1991。' },
      baseline_end: { type: 'integer', description: '30 年基准期末年，默认 2020。' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }] },
    timeoutMs: 120000,
    async execute(args, exec) {
      const invocation = buildInvocation(config.projectRoot, config.python, args);
      fs.mkdirSync(invocation.output, { recursive: true });
      const command = await ctx.subprocess.resolveExecutable(invocation.argv[0], undefined, exec.signal);
      invocation.argv[0] = command;
      appendEvidence(config.evidencePath, { event: 'analysis-start', case: invocation.case, style: invocation.style, baseline: invocation.baseline, output: invocation.output });
      const child = ctx.subprocess.spawn({
        argv: invocation.argv, cwd: invocation.cwd, signal: exec.signal, graceMs: 1000,
        stdio: { stdin: 'ignore', stdout: { maxBytes: 262144 }, stderr: { maxBytes: 65536 } },
        env: { MPLBACKEND: 'Agg', PYTHONUNBUFFERED: '1' },
      });
      const outcome = await child.done;
      await child.waitForExit(exec.signal);
      const stdout = child.collected.stdout?.readFrom(0);
      const stderr = child.collected.stderr?.readFrom(0);
      fs.writeFileSync(path.join(invocation.output, 'harness-python.stdout.txt'), stdout?.text ?? '');
      fs.writeFileSync(path.join(invocation.output, 'harness-python.stderr.txt'), stderr?.text ?? '');
      let summary = null;
      try { summary = JSON.parse(stdout?.text ?? ''); } catch {}
      let report = null;
      try { report = JSON.parse(fs.readFileSync(path.join(invocation.output, 'report.json'), 'utf8')); } catch {}
      const ok = outcome.exitCode === 0 && summary?.status === 'ok' && report?.status === 'ok';
      // Only the current successful run's declared products are scientific artifacts.
      // An error report and process logs are diagnostics, never recycled result files.
      const artifacts = ok && Array.isArray(report.artifacts) ? report.artifacts
        .filter(file => typeof file === 'string' && file === path.basename(file))
        .map(file => path.join(invocation.output, file))
        .filter(file => fs.existsSync(file) && fs.lstatSync(file).isFile()) : [];
      // The full provenance stays on disk; avoid sending thousands of historical
      // source dates back through the language model on every call.
      let modelReport = report;
      if (report?.source) {
        const { source_files, missing_dates, invalid_dates, incomplete_months, ...sourceSummary } = report.source;
        modelReport = { ...report, source: { ...sourceSummary,
          source_file_count: Array.isArray(source_files) ? source_files.length : 0,
          provenance_report: path.join(invocation.output, 'report.json') } };
      }
      const result = {
        ok, exitCode: outcome.exitCode, signal: outcome.signal,
        case: invocation.case, style: invocation.style, baseline: invocation.baseline,
        output: invocation.output, summary, report: modelReport, stdout: summary === null ? (stdout?.text ?? '') : '', stderr: stderr?.text ?? '',
        truncated: Boolean(stdout?.lossy || stderr?.lossy),
        artifacts,
        diagnostics: ['report.json', 'harness-python.stdout.txt', 'harness-python.stderr.txt']
          .map(file => path.join(invocation.output, file)).filter(file => fs.existsSync(file)),
      };
      appendEvidence(config.evidencePath, { event: 'analysis-end', case: invocation.case, output: invocation.output,
        style: invocation.style, baseline: invocation.baseline, ok: result.ok, exitCode: outcome.exitCode,
        artifactCount: result.artifacts.length, issues: report?.issues ?? summary?.issues ?? [] });
      return result;
    },
    presentCall: args => ({ card: 'generic', title: `海温分析：${args.case}`, kind: 'execute', rawInput: args }),
  });
}

export function makeStyleTool(config) {
  return defineTool({
    name: 'sst_set_style',
    description: '仅在用户要求修改 Skill 的默认绘图样式时使用。只能把 ocean-sst-analysis/SKILL.md 的单行默认样式以及 --style 示例参数在 default/presentation 之间切换；不改算法或数据。完成后重新加载 Skill。',
    parameters: { style: { type: 'string', required: true, enum: ['default', 'presentation'], description: '新的默认样式。' } },
    output: { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }] },
    async execute(args) {
      const skillPath = path.join(config.projectRoot, 'skills/ocean-sst-analysis/SKILL.md');
      if (fs.lstatSync(skillPath).isSymbolicLink()) throw new Error('不修改符号链接指向的 Skill。');
      const before = readDefaultStyle(config.projectRoot);
      const text = fs.readFileSync(skillPath, 'utf8');
      const updated = text.replace(STYLE_LINE, `默认演示样式：${args.style}`)
        .replace(/--style (?:default|presentation)\b/g, `--style ${args.style}`);
      if (updated !== text) fs.writeFileSync(skillPath, updated, 'utf8');
      const result = { before, after: args.style, changed: updated !== text, skillPath,
        sha256: createHash('sha256').update(updated).digest('hex'),
        reload: '请再次调用 skill(name="ocean-sst-analysis") 加载新正文；分析时省略 style 即使用此默认值。',
        algorithmChanged: false, dataChanged: false };
      appendEvidence(config.evidencePath, { event: 'style-updated', before, after: args.style, sha256: result.sha256 });
      return result;
    },
    presentCall: args => ({ card: 'generic', title: `修改 Skill 默认样式：${args.style}`, kind: 'edit' }),
  });
}

export function apply(ctx, config) {
  ctx.tools.register(makeAnalysisTool(ctx, config));
  ctx.tools.register(makeStyleTool(config));
  ctx.tools.guard(exec => ['skill', 'sst_analyze', 'sst_set_style'].includes(exec.name)
    ? undefined : '本演示仅允许 skill、sst_analyze 和 sst_set_style 三个受控工具。');
  ctx.on('tools/result', (exec, result) => {
    if (!['skill', 'sst_analyze', 'sst_set_style'].includes(exec.name)) return;
    const row = { event: 'tool-result', tool: exec.name, isError: result.isError };
    if (exec.name === 'skill' && !result.isError && result.value && typeof result.value === 'object') {
      row.skillName = typeof result.value.name === 'string' ? result.value.name : null;
      row.bodySha256 = typeof result.value.content === 'string' ? createHash('sha256').update(result.value.content).digest('hex') : null;
    }
    appendEvidence(config.evidencePath, row);
    return undefined;
  });
  appendEvidence(config.evidencePath, { event: 'plugin-ready', plugin: name });
}
