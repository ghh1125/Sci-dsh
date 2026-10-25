// Explicit, local user-invoked steps. Importing this file is not a workflow API.
import path from 'node:path';
import fs from 'node:fs';
import { spawn } from 'node:child_process';
import { projectRoot, resolvePython } from './runtime-paths.mjs';

try {
  const envFile = path.join(projectRoot, '.env');
  if (fs.existsSync(envFile)) process.loadEnvFile(envFile);
  const step = process.argv[2];
  const extra = process.argv.slice(3);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  let args;
  if (step === 'data') args = [path.join(projectRoot, 'scripts/fetch_noaa_oisst.py'), ...extra];
  else if (step === 'export') args = [path.join(projectRoot, 'scripts/export_skill.py'), ...extra];
  else if (step === 'compare') args = [path.join(projectRoot, 'scripts/compare_results.py'), ...extra];
  else if (step === 'skill') args = [path.join(projectRoot, 'scripts/skill_lifecycle.py'), ...extra];
  else if (step === 'test') args = ['-m', 'unittest', 'discover', '-s', 'tests', '-v', ...extra];
  else if (['plot-A', 'plot-B', 'plot-gap'].includes(step)) {
    const label = { 'plot-A': 'point_A', 'plot-B': 'point_B', 'plot-gap': 'point_B_gap_demo' }[step];
    const csv = label === 'point_B_gap_demo' ? `${label}.csv` : `${label}_monthly.csv`;
    const skillRoot = fs.existsSync(path.join(projectRoot, 'skills/ocean-sst-analysis/SKILL.md')) ? 'skills' : 'skill-templates';
    args = [path.join(projectRoot, skillRoot, 'ocean-sst-analysis/scripts/analyze_sst.py'),
      '--input', path.join(projectRoot, 'data', csv),
      '--metadata', path.join(projectRoot, 'data', `${label}.metadata.json`),
      '--baseline', '1991', '2020', '--style', 'presentation',
      '--output', path.join(projectRoot, 'outputs', `${step}-${stamp}`), ...extra];
  } else throw new Error('请选择 data、plot-A、plot-B、plot-gap、compare、skill、export 或 test。');
  const child = spawn(resolvePython(), args, { cwd: projectRoot, env: process.env, stdio: 'inherit' });
  child.on('error', error => { console.error(`无法启动 Python：${error.message}`); process.exitCode = 1; });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('close', code => { process.exitCode = code ?? 1; });
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
