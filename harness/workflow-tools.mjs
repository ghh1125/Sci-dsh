import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { evidence, projectPath, readJson, runPython, simpleName, successfulRun,
  skillBodyHash, waitForSkillBody, currentDataOperation, withDataOperation } from './tool-common.mjs';
import { figureLinks } from './gallery.mjs';

const { defineTool } = await import(process.env.SCI_DSH_TOOLS_MODULE
  ? pathToFileURL(process.env.SCI_DSH_TOOLS_MODULE).href : '@deepseek-ai/dsh-tools');
const NAME = 'ocean-sst-analysis';
const jsonOutput = { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }] };

function tool(name, description, parameters, execute, timeoutMs = 120000) {
  return defineTool({ name, description, parameters, execute, output: jsonOutput, timeoutMs,
    presentCall: args => ({ card: 'generic', title: description.split('。')[0], rawInput: args }) });
}

export function makeWorkflowTools(ctx, config) {
  const root = config.projectRoot;
  return [
    tool('sst_status', '查看海温数据、专用 Skill 和已有结果的准备状态。只检查本地文件，不访问模型或数据源。', {}, async () => {
      const skill = projectPath(root, 'skills', NAME, 'SKILL.md');
      const data = {};
      for (const point of ['A', 'B']) {
        const file = projectPath(root, 'data', `point_${point}_monthly.csv`);
        const meta = projectPath(root, 'data', `point_${point}.metadata.json`);
        data[point] = { csvExists: fs.existsSync(file), metadataExists: fs.existsSync(meta) };
      }
      const runs = [];
      const out = projectPath(root, 'outputs');
      if (fs.existsSync(out)) for (const entry of fs.readdirSync(out, { withFileTypes: true })) {
        if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
        try { const { report } = successfulRun(root, entry.name); runs.push({ run: entry.name, baseline: report.baseline, point: report.point, style: report.style }); } catch {}
      }
      const dataReady = Object.values(data).every(item => item.csvExists && item.metadataExists);
      return { ok: true, data, dataOperation: currentDataOperation(root), skillInstalled: fs.existsSync(skill),
        skillName: NAME, draftExists: fs.existsSync(projectPath(root, 'drafts', NAME, 'SKILL.md')),
        archiveExists: fs.existsSync(projectPath(root, 'dist', `${NAME}.zip`)), runs,
        next: !dataReady ? '数据尚未准备齐全。用户要求准备数据时调用sst_prepare_data；不要用sst_analyze代替下载。'
          : fs.existsSync(skill) ? '先加载专用 Skill，再执行分析。' : '可先直接分析 A；成功后创建、导出和安装 Skill。' };
    }),
    tool('sst_prepare_data', '下载并准备两份真实 NOAA 海温数据和缺月教学副本。只有用户要求准备数据时调用；首次可能耗时数分钟，成功缓存可复用。', {
      workers: { type: 'integer', description: '并发数1–4，默认1；不因网络故障自动提高并发。' },
      timeout_seconds: { type: 'integer', description: '单次请求超时30–120秒，默认60。' },
      attempts: { type: 'integer', description: '每次请求最多尝试1–4次，默认3；本工具总下载预算固定1200秒。' },
      offline: { type: 'boolean', description: '只检查并复用本地完整数据或缓存，不发出网络请求；默认false。' },
      refresh: { type: 'boolean', description: '明确要求重新下载时才设true；默认续用缓存。' },
    }, async (args, exec) => {
      const workers = args.workers ?? 1, timeout = args.timeout_seconds ?? 60, attempts = args.attempts ?? 3;
      if (!Number.isInteger(workers) || workers < 1 || workers > 4) throw new Error('workers 必须为1–4。');
      if (!Number.isInteger(timeout) || timeout < 30 || timeout > 120) throw new Error('timeout_seconds 必须为30–120。');
      if (!Number.isInteger(attempts) || attempts < 1 || attempts > 4) throw new Error('attempts 必须为1–4。');
      if (args.offline === true && args.refresh === true) throw new Error('offline 与 refresh 不能同时为true。');
      return withDataOperation(root, 'prepare', async () => {
        projectPath(root, 'data', 'raw');
        const params = ['--workers', String(workers), '--timeout', String(timeout), '--attempts', String(attempts),
          '--budget-seconds', '1200', '--data-dir', projectPath(root, 'data')];
        if (args.refresh === true) params.push('--refresh');
        if (args.offline === true) params.push('--offline');
        const source = String(process.env.SCI_DSH_DATA_SOURCE ?? '').trim().toLowerCase();
        const downloader = source === 'psl-monthly' ? 'fetch_noaa_psl_monthly.py' : 'fetch_noaa_oisst.py';
        const result = await runPython(ctx, config, exec, downloader, params, 'sst_prepare_data');
        const payload = result.result;
        const mode = ['local-cache', 'download', 'offline', 'psl-monthly'].includes(payload?.mode) ? payload.mode
          : args.offline === true ? 'offline' : 'download';
        if (!result.ok || payload?.status !== 'ok') {
          const error = result.error ?? { category: 'DOWNLOAD_PROTOCOL_ERROR',
            message: '数据准备没有返回可确认成功的最终JSON。', retryable: false,
            action: '查看本次日志；不要开始分析或无条件重试下载。' };
          return { ...result, ok: false, mode, error, artifacts: [],
            note: '准备未成功。报告error的类别与建议；只有用户明确要求继续时才重试，不用分析工具代替数据准备。' };
        }
        let checks = payload.validation;
        try { if (!Array.isArray(checks)) checks = readJson(projectPath(root, 'data', 'data_validation.json')); }
        catch { checks = null; }
        if (!Array.isArray(checks) || checks.length !== 2 || !checks.every(row => row?.complete === true && row.monthly_rows === 528)) {
          return { ...result, ok: false, mode, validation: checks, artifacts: [],
            error: { category: 'DATA_VALIDATION_FAILED', message: '完整性报告没有证明两个格点均有528个完整月均值。',
              retryable: false, action: '查看验证报告并解决数据完整性问题后，再请求准备或分析。' } };
        }
        return { ...result, mode, validation: checks,
          files: ['point_A_monthly.csv', 'point_B_monthly.csv', 'point_B_gap_demo.csv'],
          source: payload?.source ?? 'https://www.ncei.noaa.gov/products/optimum-interpolation-sst',
          note: '月数据和配套metadata已生成；分析仍会核对SHA、单位与基准期。' };
      });
    }, 1800000),
    tool('sst_compare_results', '实际比较两次结果，检查仅样式改变或基准期变更是否符合数值规则。', {
      first: { type: 'string', required: true, description: '第一次输出子目录名。' },
      second: { type: 'string', required: true, description: '第二次输出子目录名。' },
      mode: { type: 'string', enum: ['style', 'baseline'], description: 'style要求数值表完全相同；baseline核对参照期变化恒等式。' },
    }, async (args, exec) => {
      const a = successfulRun(root, args.first).output, b = successfulRun(root, args.second).output;
      const mode = args.mode ?? 'style';
      if (!['style', 'baseline'].includes(mode)) throw new Error('不支持该比较模式。');
      return runPython(ctx, config, exec, 'compare_results.py', [a, b, '--mode', mode], 'sst_compare_results');
    }),
    tool('sst_create_skill', '从一次真实成功分析创建可复用 Skill 草稿。使用项目科学方法模板和运行证据，不把失败记录包装成技能。', {
      run: { type: 'string', required: true, description: '成功分析的输出子目录。' },
    }, async (args, exec) => {
      const fromRun = successfulRun(root, args.run).output;
      projectPath(root, 'drafts', NAME);
      return runPython(ctx, config, exec, 'skill_lifecycle.py', ['create', '--from-run', fromRun, '--name', NAME], 'sst_create_skill');
    }),
    tool('sst_export_skill', '将草稿或已安装 Skill 导出为ZIP以备导入或换设备复用。只打包方法、脚本和必要参考，不带API密钥或原始数据。', {
      source: { type: 'string', enum: ['draft', 'installed'], description: '首次生成后用draft，修改默认样式后用installed。' },
      archive: { type: 'string', description: 'dist下不含扩展名的包名，默认ocean-sst-analysis；已有包不覆盖。' },
    }, async (args, exec) => {
      const source = args.source ?? 'installed';
      if (!['draft', 'installed'].includes(source)) throw new Error('source 必须为 draft 或 installed。');
      const archive = simpleName(args.archive ?? NAME, '包名');
      return runPython(ctx, config, exec, 'skill_lifecycle.py', ['export', '--source', source, '--name', NAME,
        '--output', projectPath(root, 'dist', `${archive}.zip`)], 'sst_export_skill');
    }),
    tool('sst_install_skill', '导入本项目生成的ZIP或安装草稿，让Harness发现该Skill。替换已有版本仅在用户明确要求时使用replace，保留备份。', {
      source: { type: 'string', enum: ['archive', 'draft'], description: '默认archive。' },
      archive: { type: 'string', description: 'dist下不含扩展名的ZIP包名，默认ocean-sst-analysis。' },
      replace: { type: 'boolean', description: '用户明确要求更新已安装版本时才true。' },
    }, async (args, exec) => {
      const source = args.source ?? 'archive';
      if (!['archive', 'draft'].includes(source)) throw new Error('source 必须为 archive 或 draft。');
      const params = ['install', '--name', NAME];
      if (source === 'draft') params.push('--draft', NAME);
      else params.push('--archive', projectPath(root, 'dist', `${simpleName(args.archive ?? NAME, '包名')}.zip`));
      projectPath(root, 'skills', NAME);
      if (args.replace === true) params.push('--replace');
      const result = await runPython(ctx, config, exec, 'skill_lifecycle.py', params, 'sst_install_skill');
      if (!result.ok) return { ...result, ready: false, next: '安装失败，勿声称Skill已可用。' };
      const skillFile = projectPath(root, 'skills', NAME, 'SKILL.md');
      const text = fs.readFileSync(skillFile, 'utf8');
      const bodyHash = skillBodyHash(text);
      const ready = await waitForSkillBody(ctx, config, exec, NAME, skillFile, bodyHash);
      evidence(config, { event: 'skill-installed', name: NAME, ready, bodySha256: bodyHash });
      return { ...result, installed: true, ready,
        next: ready ? '已被Harness发现。现在调用 skill(name="ocean-sst-analysis") 加载，然后复用。'
          : '文件已安装，但当前会话尚未发现新版本；新建/刷新Harness会话后加载，勿声称已完成加载。' };
    }),
    tool('sst_show_figure', '展示成功分析生成的图片，返回Harness内部图库链接和可下载文件。不会为失败运行制造预览。', {
      run: { type: 'string', required: true, description: '成功分析的输出子目录。' },
    }, async args => {
      simpleName(args.run, '运行目录');
      const result = figureLinks(root, args.run, config.webURL || undefined);
      evidence(config, { event: 'figure-shown', tool: 'sst_show_figure', run: args.run });
      return { ok: true, ...result };
    }),
  ];
}
