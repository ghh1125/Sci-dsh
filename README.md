# Sci-dsh：在 DeepSeek Harness 里完成海温分析与 Skill 复用

面向南海所科学智能论坛的演示代码：**准备真实海温数据 → 分析出图 → 形成 Skill → 导入 → 修改并复用 → 检查错误输入。**

所有科研业务都通过 **DeepSeek Harness（DSH）的对话和工具调用**完成。Python 是 Harness 调用的计算、绘图工具；Skill 保存方法、规则、脚本和成功运行记录。首次安装依赖、填写 API 配置在启动前完成。项目运行不需要 Codex。

> **交付状态：本机安装与网页启动已验证，端到端科学演示仍被数据获取阻塞。** Windows 本机已启动 Harness、验证模型 API 与网页流程，并修复网页内测声明无法保存的问题；但 NOAA ERDDAP 数据下载未成功，A、B 和缺月教学 CSV 尚未生成，因此没有把缺失数据当作科学结果。详细记录见 [STATUS.md](STATUS.md)。仓库没有 API 密钥，也没有用虚构数据制作的预览图。

本次针对 Issue #1 补上下载容错、错误诊断、离线数据复用、数据准备前置检查和 Windows ZIP 原始路径校验。**这些修复尚未运行复测**，不把以前版本的通过数字当作本次通过结果。

## 1. 需求落实清单

| 原要求 | 已写入的功能 | 如何操作 |
| --- | --- | --- |
| 只能用 DeepSeek Harness | 官方 DSH 启动器、项目工具插件、网页对话 | `npm run web` |
| 给出可用工具和真实数据集 | NOAA OISST 下载、质控、月均聚合；Python / Matplotlib 绘图 | 对话中准备数据 |
| 分析海温、展示图片 | 同月气候态、月距平、PNG / SVG / PDF、Harness 内部图库 | 对话中分析并展示 |
| 把成功流程变成 Skill | 方法模板＋经校验的成功记录，生成草稿 | 对话中创建 Skill |
| 导出、导入、修改、复用 | ZIP 导出、安装、重新加载、默认样式修改、B 格点复用 | 对话中逐步操作 |
| 验证修改没有改错数值 | 样式比较、基准期切换比较 | 对话中比较两次结果 |
| 演示智能体发现问题 | 故意缺少 2000-02 的教学副本，拒绝出图 | 对话中分析缺月案例 |
| 换电脑可使用 | 依赖锁文件、配置模板、完整步骤 | 克隆本仓库后配置 |
| 现在不要跑 | 只交付源码和说明，无自动运行 CI | 后续自行运行验收 |

这些是**实现覆盖情况**；实际能否完整运行，还需要在允许运行时验证 DSH、代理服务和 NOAA 网络访问。

## 2. 安装 DSH 和依赖

准备 Git、Node.js 22 或更新版本、Python 3.10 或更新版本，以及支持 **OpenAI 兼容 Chat Completions、流式输出、工具调用**的模型服务。

```bash
git clone https://github.com/ghh1125/Sci-dsh.git
cd Sci-dsh
npm ci
```

`npm ci` 会安装项目固定版本的官方 DeepSeek Harness，无需另外全局安装 `dsh`。当前固定 DSH CLI `0.1.0-rc.6` 与工具包 `0.1.0-rc.7`；不要在演示前随意升级。接口按本机已有官方包源码编写，最终组合仍待运行验收。

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Python 工具依赖见 `requirements.txt`：Matplotlib 绘图，NumPy 支持数值环境，certifi 提供 HTTPS 证书。项目自动使用自己的 `.venv`，不必每次手动激活。Windows 路径已在本机验证。

## 3. 配置 API

用编辑器打开本地 `.env`：

```dotenv
SCI_DSH_BASE_URL=https://token.fncompute.com/v1
SCI_DSH_MODEL=服务商提供的准确模型ID
SCI_DSH_API_KEY=仅在本机填写你的密钥
```

- 本机验证使用的服务入口是 `https://token.fncompute.com/v1`；该地址已通过模型列表、流式输出和工具调用请求验证。换设备时仍应以服务控制台或文档给出的 OpenAI 兼容 API base URL 为准。
- 模型 ID 必须从控制台复制，不猜测名称。模型和代理都需要支持工具调用。
- 当前本机已验证代理和模型的流式输出、工具调用可用；这不代表 NOAA 数据下载网络一定可用。
- `.env` 被 Git 忽略；不要把 key 写入 Skill、提示词或 GitHub。新设备重新填写即可。
- 如需使用其他 Python，在 `.env` 加 `SCI_DSH_PYTHON=/你的/python/完整路径`。

之后可以主动执行：

```bash
npm run harness:check
```

它检查本地依赖和配置，**不调用模型 API，也不能证明 API 连通**。首次显示 `skillInstalled: false` 是正常状态：专用 Skill 要在首次成功分析后创建。

## 4. 打开 Harness

```bash
npm run web -- --port 3080
```

在浏览器打开终端显示的 `http://127.0.0.1:3080`。之后在这个对话窗口输入下面的指令，无需另开终端执行科研脚本。网页仅监听本机；按 Ctrl-C 停止。

**数据准备最好在演讲前完成一次。** 首次需要联网下载；现场从状态检查开始，复用已验证的数据缓存。后续模型对话会调用配置的 API。

## 5. 现场一步一步怎么做

以下输出名适合第一次演示。重新演示请换新名字，例如把 `demo-A-v1` 改为 `demo-A-v3`。非空结果目录、已有草稿和已有 ZIP 默认不覆盖。

### ① 准备数据

在 Harness 输入：

> 调用 sst_status 查看状态，然后调用 sst_prepare_data 准备两个格点的数据和缺月教学副本，workers=1、timeout_seconds=90。已有完整数据先校验复用。成功后展示来源和完整性报告；失败就展示 error.category、message 和 action，不调用 sst_analyze。

预期调用：`sst_status → sst_prepare_data`。展示两个格点各 **528 个月**的完整性结果。已有文件不代表已通过完整性校验，首次分析仍会严格复查。

| 数据 | 格点 | 覆盖期和用途 |
| --- | --- | --- |
| A | 18.125°N，114.125°E | 1982–2025，首次分析 |
| B | 15.125°N，114.125°E | 1982–2025，换输入复用 |
| missing-month | 与 B 相同 | 人为删除 2000-02，用于教学 |

来源是 [NOAA OISST](https://www.ncei.noaa.gov/products/optimum-interpolation-sst) 格点分析产品。它们不是观测站，也不是南海区域平均。脚本下载日值，只有自然月全部日期有效才计算完整月均值。

下载中断可在 Harness 中要求“继续准备数据，复用缓存”。历史镜像缺项仅在 2016 年以前、官方说明 v2 与 v2.1 SST 相同的区间，核对重叠值后使用有出处的 v2 数据补充，并记录具体来源。2016 年以后不采用旧版补充；未解决的缺值会报错。详见 [数据说明](data/README.md)。

下载先检查一个待获取的年份，成功后才处理其余年份；失败时及时返回诊断，不让模型用分析工具代替数据准备。只对超时、连接中断、限流和部分服务器错误退避重试；证书、认证、无效请求和错误 CSV 不盲目重试。成功缓存保留，损坏的年度缓存单独重新获取。默认整体网络预算 1200 秒，首个年份预算最多 180 秒；系统 DNS 阻塞仍可能受操作系统超时影响。

**网络/SSL 报错时这样处理：**

1. 先看 Harness 返回的错误类别。模型 API 可用，不代表能访问 NOAA；`SCI_DSH_BASE_URL` 不能当作数据下载代理。
2. 如果电脑已有可访问 NOAA 的 HTTP/HTTPS 代理，在本地 `.env` 加 `SCI_DSH_DATA_PROXY=http://127.0.0.1:实际端口`。端口从代理软件读取，不能照抄示例；没有代理就不要填。
3. 如果机构网络需要额外 CA，把管理员提供的 PEM 证书路径填入 `SCI_DSH_DATA_CA_BUNDLE`。默认会保留系统证书，并追加 certifi；不关闭 HTTPS 校验。
4. 修改 `.env` 后停止并重新启动 `npm run web`，再发送上面的准备指令。不要设置 `refresh: true`，这样会保留已成功缓存。

如果该网络始终无法访问 NOAA，可以在一台能访问的电脑准备好真实数据，将完整 `data/` 复制到演示电脑，然后在 Harness 输入：

> 调用 sst_prepare_data，offline=true。只校验现有数据，必要时从本地原始缓存重新聚合，不访问任何数据服务器；通过后再继续。

离线模式仍核对 CSV/metadata、坐标、覆盖期、单位、日覆盖和 SHA；缺文件或无效输入会失败，不生成模拟海温。数据离线不等于模型离线，DSH 对话仍需要你的模型 API。两个默认下载域名同属 NOAA 的 ERDDAP 服务，域名轮换不能保证绕过该服务或本机网络故障。

### ② 先用工具完成一次分析、看到图

> 分析 A，基准期 1991–2020，本次使用 default 样式，输出到 demo-A-v1。若已有 ocean-sst-analysis 就先加载；如果还没有，就使用基础分析工具。成功后展示图片，给出来源和一个月份的距平计算；失败就停止。

预期调用：`sst_analyze → sst_show_figure`。点击返回的 Harness 内部图库链接，可看到：

- 上图：2025 年各月海温与基准期同月平均的对照。
- 下图：1982–2025 年月海温异常。
- 同页的坐标、基准期、中文结果说明，以及 PNG / SVG / PDF / CSV 下载链接。

这里应展示**本次实际生成的图片**。现在仓库没有预制图片；仅有代码，不提前声称已经出图。图库由 DSH 的 Web 服务提供，无需单独启动图片服务器。

### ③ 把成功流程生成 Skill

> 从 demo-A-v1 的成功分析创建 ocean-sst-analysis Skill 草稿，展示生成位置和保存的运行证据。不要把失败记录或尚未运行的分析作为依据。

调用 `sst_create_skill`，生成 `drafts/ocean-sst-analysis/`。创建工具会核对输入 CSV、metadata、分析脚本的 SHA-256，以及产物完整性和科学检查项。

**这里的“生成”是把预先编写的方法模板与真实成功记录组合成可复用包**，包含规则、脚本、方法说明和示例运行记录。它不表示模型从零发现了统计方法，也不训练模型。

### ④ 导出，再导入到 Harness

先输入：

> 将刚创建的 Skill 草稿导出，source 使用 draft，包名 ocean-sst-analysis。

调用 `sst_export_skill`，生成 `dist/ocean-sst-analysis.zip`。再输入：

> 从包名 ocean-sst-analysis 的 ZIP 导入 Skill。确认 Harness 已发现它，再加载 ocean-sst-analysis。

预期调用：`sst_install_skill → skill`。安装后目录为 `skills/ocean-sst-analysis/`；只有真实加载成功，才能说“已加载”。若文件已安装但当前会话未发现新版本，按工具提示新建/刷新会话后再加载。

### ⑤ 修改 Skill 的默认样式，比较数值

> 把 ocean-sst-analysis 的默认样式改为 presentation，重新加载最新 Skill。仍用 A 和 1991–2020 基准期，输出到 demo-A-v2；这次省略 style，让工具读取修改后的默认值。展示图片，再将 demo-A-v1 与 demo-A-v2 按 style 模式比较。

预期调用：`sst_set_style → skill → sst_analyze → sst_show_figure → sst_compare_results`。

展示默认样式真实修改、字体和配色变化，以及**两份数值表是否逐字节一致**的比较结果。只有比较成功，才能说“样式改变没有改变数值”。

### ⑥ 新建会话，换一个格点复用

在 Harness 网页新建会话，然后输入：

> 加载 ocean-sst-analysis，分析 B，基准期 1991–2020，输出到 demo-B-v1，使用 Skill 的默认样式，成功后展示图片。

展示新会话直接加载 Skill、继承保存的默认样式、为 B 重新计算气候态和距平。无需重新粘贴整套方法。

### ⑦ 给错误数据，看它停止

> 这是人为删月的教学测试。加载同一个 Skill，分析 missing-month，基准期 1991–2020，输出到 demo-gap-v1。必须实际校验；失败后指出具体问题，不补值、不缩短基准期、不切换数据，不展示旧图。

应准确报告缺少 **2000-02**，二月基准样本不足，停止计算且不生成科学图片。分析工具退出码 2 表示输入未通过；这一步的教学成功指“实际检出指定错误”，不是得到一张图。

### ⑧ 可选加演：修改基准期

> 加载 Skill，分析 A，基准期改为 1982–2011，仍用 presentation，输出到 demo-A-baseline。展示图片，再将 demo-A-v2 与 demo-A-baseline 按 baseline 模式比较。

展示 SST 原值不变，气候态和距平随基准期改变。比较工具会核对同一输入、同一方法，按各自基准重算气候态，检查每月距平及两次差值关系。

### ⑨ 带走修改后的 Skill

> 导出已安装的 Skill，source 使用 installed，包名 ocean-sst-analysis-presentation。

得到 `dist/ocean-sst-analysis-presentation.zip`。这次应导出 **installed**，而不是修改前的 draft。ZIP 不带 API key、原始数据、图片和电脑专属依赖。

## 6. 换设备继续使用

1. 克隆仓库，按第 2–3 节安装并配置。
2. 若要沿用修改后的 Skill，把导出的 ZIP 复制到新项目的 `dist/`。
3. 启动 Harness，输入：“从包名 ocean-sst-analysis-presentation 导入 Skill，确认可发现后加载。”
4. 在 Harness 中准备数据，或事先复制旧设备的完整 `data/`（包含 CSV、metadata 和来源记录）；分析仍会验证文件配对与完整性。
5. 开始分析。若已有同名 Skill，只有明确要求替换才使用 `replace: true`；旧版保存在 `backups/`。

**最稳妥的迁移单位是整个仓库＋依赖＋配置，必要时再导入 ZIP。** 只复制 `SKILL.md` 不会自动安装 Python 或注册专用工具。这个示例的工具只接收 A、B、missing-month；分析自己的数据需按字段契约准备 CSV/metadata，并扩展案例映射。

## 7. 工具、方法和输出

| Harness 工具 | 作用 |
| --- | --- |
| `sst_status` | 查看本地准备状态，不替代科学校验 |
| `sst_prepare_data` | 下载、聚合、保存来源与完整性报告 |
| `sst_analyze` | 校验输入、计算气候态和距平、绘图 |
| `sst_show_figure` | 返回内部图库和真实产物链接 |
| `sst_compare_results` | 样式或基准期变化的数值验收 |
| `sst_create_skill` | 从成功运行和模板生成草稿 |
| `sst_export_skill` | 导出草稿或已安装版本 |
| `sst_install_skill` | 从 ZIP 或草稿安装、发现 Skill |
| `skill` | DSH 官方 Skill 加载工具 |
| `sst_set_style` | 修改已安装 Skill 的默认样式 |

科学公式：同月气候态＝基准期内 30 个对应月份月均 SST 的算术平均；月距平＝当月 SST－同月气候态。检查月份唯一连续、自然月日覆盖完整、单位、坐标、文件 SHA 和完整 30 年基准。不能用所有月份的总平均代替同月气候态。

每次成功分析生成：

| 文件 | 用途 |
| --- | --- |
| `sst_analysis.png / .svg / .pdf` | 展示图片、后续编辑或放入报告 |
| `result.csv` | 每月 SST、气候态和距平 |
| `climatology.csv` | 12 个历月的气候态、样本数 |
| `report.json` | 状态、来源、参数、校验值、数值抽查 |
| `summary.md` | 中文结果说明、图注、解释范围 |

只描述这个格点和这段时间。本示例不判定海洋热浪、不计算趋势显著性、不作因果归因；中文图注是计算结果的辅助表达，研究者仍需核对。

## 8. 不用网页时，也由 DSH 执行

以下命令仍启动 DeepSeek Harness，由模型调用同一组工具，不是直接绕过 Harness 跑分析：

```bash
npm run dsh -- --prompt "准备海温数据，成功后报告完整性"
npm run demo -- --case A --style default --output cmd-A-v1
npm run dsh -- --prompt "从 cmd-A-v1 创建 Skill 草稿，再导出草稿为 ocean-sst-analysis"
npm run dsh -- --prompt "导入包名 ocean-sst-analysis，确认可发现后加载"
npm run demo -- --set-style presentation --case A --output cmd-A-v2
npm run dsh -- --prompt "按 style 模式比较 cmd-A-v1 和 cmd-A-v2"
npm run demo -- --case B --output cmd-B-v1
npm run demo -- --case missing-month --output cmd-gap-v1
npm run demo -- --case A --baseline 1982 2011 --output cmd-A-baseline
```

每条命令创建独立任务；需要已安装 Skill 的分析会先加载。`--style` 只影响本次，`--set-style` 修改保存的默认值，两者不能同时用。通用 `--prompt` 的任务、样式和输出请完整写在提示词里。

无网页模式时，`sst_show_figure` 返回本地路径；要在 Harness 图库查看，可启动 Web 后再要求展示该输出目录。默认任务超时 1800 秒，可用 `--timeout` 调整，范围 1–3600 秒。

## 9. 验收与排错

证据位于 `outputs/harness-evidence/<启动时间>/`：`run.json`、`tool-events.jsonl`、日志、结束时的 `verification.json`。验收必须看实际工具调用和结果，不能仅凭模型说“已完成”。网页模式的会话日志在退出后汇总；中途可看工具卡和结果报告。

预设命令会检查要求的案例、输出、基准、样式、Skill 加载顺序和真实产物展示；修改或导入后不能用旧正文的加载记录冒充新版本。通用自然语言任务只校验实际发生的工具执行，不保证理解了任意提示词的全部意图，讲者仍应对照步骤确认。

之后允许测试时，可执行下列**维护检查**（不请求模型 API，不作为现场业务入口）：

```bash
npm run test:analysis
npm run test:harness
```

测试使用人工数值校验公式和失败行为，不把测试数据当作真实演示数据。仓库保留本地维护脚本供开发排错；主演示所有业务由 Harness 调用。

| 现象 | 处理 |
| --- | --- |
| 找不到 DSH | 在项目根目录执行 `npm ci` |
| Python 依赖缺失 | 用项目 `.venv` 安装 `requirements.txt` |
| 缺模型名或 key | 填写本地 `.env`，不修改 Skill |
| 401 / 403 | 在服务控制台核对 key、额度、模型权限 |
| 404 或模型不存在 | 核对完整 API 入口和准确模型 ID |
| 模型只聊天，不调用工具 | 核对代理与模型的工具调用支持；文字回答不算执行 |
| NOAA 下载慢或中断 | 查看返回的类别和建议；按第 5 节配置数据代理/机构 CA，或复用完整离线数据 |
| `DATA_NOT_PREPARED` | 先调用 `sst_prepare_data`；分析不会自动下载、补值或把缺文件当作缺月教学成功 |
| 输出目录非空 | 换新输出名，不重用旧图 |
| 草稿或 ZIP 已存在 | 继续使用已创建草稿；导出换新包名，默认不覆盖 |
| 已安装但未发现新 Skill | 按返回提示新建/刷新 Harness 会话再加载 |
| 图库链接打不开 | 保持 Web 在运行，使用本次返回的 `127.0.0.1` 地址和端口 |
| 参数比较失败 | 看具体差异，不把失败报告包装成“数值相同” |

## 10. 代码位置

```text
harness/sci.patch.yml                   模型、技能发现、科研工具配置
harness/sci.web.patch.yml               DSH 网页配置
harness/ocean-tools.mjs                 分析与样式工具
harness/workflow-tools.mjs              数据、比较、Skill 生命周期、展示工具
harness/gallery.mjs                     DSH 内部图库
harness/verify-workflow.mjs             执行证据验收
scripts/run-harness.mjs                 统一 DSH 启动入口
scripts/fetch_noaa_oisst.py              NOAA 下载、月聚合、来源记录
scripts/download_network.py             HTTPS、系统/机构 CA、数据代理、错误分类
scripts/compare_results.py              两次分析的数值比较
scripts/skill_lifecycle.py              创建、导出、安装 Skill
skill-templates/ocean-sst-analysis/      初始方法模板，不是已安装 Skill
skills/                                运行后安装的 Skill
drafts/                                运行后生成的草稿
dist/                                  运行后导出的 ZIP
data/                                  数据与来源文件
outputs/                               图片、表格、报告与执行证据
tests/                                 后续主动运行的验收检查
```

更详细的现场提示见 [演示提示](skill-templates/ocean-sst-analysis/references/demo-prompts.md)，方法与修改规则见 [Skill 模板](skill-templates/ocean-sst-analysis/SKILL.md)。运行目录、密钥和数据缓存均不提交 Git；没有提交后自动调用 API 或生成图片的任务。
