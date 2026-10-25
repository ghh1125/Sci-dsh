import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function inspectModules(modules) {
  try {
    const base = path.join(modules, '@deepseek-ai');
    const cliRoot = path.join(base, 'dsh');
    const cli = JSON.parse(fs.readFileSync(path.join(cliRoot, 'package.json'), 'utf8'));
    const toolsRoot = path.join(base, 'dsh-tools');
    const tools = JSON.parse(fs.readFileSync(path.join(toolsRoot, 'package.json'), 'utf8'));
    if (cli.name !== '@deepseek-ai/dsh' || cli.version !== '0.1.0-rc.6') return null;
    if (tools.name !== '@deepseek-ai/dsh-tools' || tools.version !== '0.1.0-rc.7') return null;
    return { modules, cliRoot, cli: path.join(cliRoot, 'lib/bin.js'), tools: path.join(toolsRoot, 'lib/index.js'), version: cli.version, toolsVersion: tools.version };
  } catch { return null; }
}

export function findDshRuntime() {
  const candidates = [path.join(projectRoot, 'node_modules')];
  if (process.env.SCI_DSH_NODE_MODULES) candidates.push(path.resolve(process.env.SCI_DSH_NODE_MODULES));
  // A previously installed official npx distribution is a local fallback only.
  const cache = path.join(os.homedir(), '.npm', '_npx');
  try { for (const dir of fs.readdirSync(cache).sort()) candidates.push(path.join(cache, dir, 'node_modules')); } catch {}
  for (const candidate of candidates) {
    const found = inspectModules(candidate);
    if (found) return found;
  }
  throw new Error('没有找到已验证版本的 DeepSeek Harness。请在项目目录运行 npm ci。');
}

export function configureToolsImport(runtime) {
  process.env.SCI_DSH_TOOLS_MODULE = runtime.tools;
}

export function resolvePython() {
  if (process.env.SCI_DSH_PYTHON || process.env.SST_PYTHON) return process.env.SCI_DSH_PYTHON || process.env.SST_PYTHON;
  const local = path.join(projectRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return fs.existsSync(local) ? local : (process.platform === 'win32' ? 'python' : 'python3');
}
