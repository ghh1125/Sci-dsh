# Sci-dsh：用 DeepSeek Harness 做海温分析

这个项目用于南海所科学智能论坛的现场演示：**给一份海温数据 → 调用工具计算和画图 → 修改 Skill → 换一份数据复用 → 检查错误输入。**

运行平台只有 **DeepSeek Harness**。Harness 调度工作，Python 工具负责数值计算和绘图，Skill 保存方法和检查规则。

> **交付状态：源码准备版。** 按要求，最后阶段只编写代码，没有正式启动 Harness、调用模型 API、完成全部数据下载或生成示例图片。最终代码和新设备安装流程尚未做端到端运行验收。下面的命令由使用者准备好后主动执行；预期效果不代表已实测结果。仓库没有 API 密钥，也没有用虚构数据制作的预览图。

## 1. 需要准备什么

| 项目 | 要求 |
| --- | --- |
| 系统 | macOS、Linux；Windows 可按下面的 PowerShell 命令配置，尚未实机验证 |
| Node.js | 22 或更新版本，带 npm |
| Python | 3.10 或更新版本，能创建虚拟环境 |
| Git | 用于下载项目和之后更新 |
| 模型服务 | 支持 OpenAI 兼容 Chat Completions、流式输出和工具调用的服务 |
| API 配置 | API base URL、准确模型 ID、你自己的 key |
| 网络 | 首次安装依赖、下载 NOAA 数据及调用在线模型时需要 |

依赖版本固定在 `package-lock.json`：DSH CLI 为 `0.1.0-rc.6`，工具包为 `0.1.0-rc.7`。这是根据本机已有官方包接口编写的组合，不自动跟随最新版升级。Python 依赖见 `requirements.txt`。

## 2. 换设备后安装

以下命令在你准备运行时执行。克隆到任意位置即可，不依赖原电脑的绝对路径。

```bash
git clone https://github.com/ghh1125/Sci-dsh.git
cd Sci-dsh
npm ci
```

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

项目会优先使用自己的 `.venv`，不要求每次手动激活它。也可以在 `.env` 中用 `SCI_DSH_PYTHON` 指定其他已装好依赖的 Python。

## 3. 配置 API

用编辑器打开本地 `.env`，填写三项：

```dotenv
SCI_DSH_BASE_URL=https://token.fncompute.com/v1
SCI_DSH_MODEL=这里填写服务商提供的准确模型ID
SCI_DSH_API_KEY=这里仅在本地填写你的密钥
```

- 目前已知的服务入口是 `https://token.fncompute.com`。上面的 **`/v1` 只是待确认示例**，没有发送请求验证；请以该服务控制台或文档给出的 OpenAI 兼容 API base URL 为准。
- 模型 ID 从服务控制台复制，本项目不猜测默认模型；模型需要支持工具调用。
- `.env` 自动读取且被 Git 忽略。换设备后重新填写，不需要把密钥上传 GitHub，也不依赖旧电脑的 `~/.dsh` 配置。
- 这里采用 DSH 的 OpenAI 兼容适配层，仍由 DeepSeek Harness 运行。该代理的流式输出与工具调用兼容性尚未验证。

之后可执行本地检查：

```bash
npm run harness:check
```

它检查依赖、Skill 文件和配置是否存在，不请求模型、不检验 key 是否有效，也不验证代理连通性。

## 4. 准备真实海温数据

```bash
npm run data:prepare
```

这一步只访问 NOAA 公共数据，不使用模型 API。它按年下载两个格点的日值，检查日期与单位，再生成月均 CSV：

| 数据 | 格点 | 时间与作用 |
| --- | --- | --- |
| A | 18.125°N，114.125°E | 1982–2025，首次分析 |
| B | 15.125°N，114.125°E | 同样时间范围，新任务复用 |
| B 缺月教学副本 | 与 B 相同 | 故意删去 `2000-02`，用于演示拒绝错误输入 |

完整数据每个格点有 528 个月。它们是 NOAA OISST 格点分析产品，**不是观测站，也不是整个南海的平均值**。正式演讲前还应核对格点位置和数据适用性。

预期生成：

```text
data/
  point_A_monthly.csv
  point_A.metadata.json
  point_B_monthly.csv
  point_B.metadata.json
  point_B_gap_demo.csv
  point_B_gap_demo.metadata.json
  data_validation.json
  raw/                       原始响应与来源记录
```

下载中断后，重新运行同一命令可使用已完成的缓存。网络慢时可降低并发、增加单次超时：

```bash
npm run data:prepare -- --workers 2 --timeout 90
```

历史镜像可能缺文件。脚本只对 2016 年以前、NOAA 明确说明 v2 与 v2.1 SST 相同的区间，使用有出处的历史 v2 记录补充镜像缺项，并检查可用重叠值和记录使用日期。这不是插值；2016 年以后不采用旧版补充。可用 `--no-historical-repair` 关闭该补充路径。未解决的缺项会报错，不会制造海温值。

原始缓存、下载后的数据、运行结果均不提交 Git。有关字段和来源见 [数据说明](data/README.md)。

## 5. 用 Harness 完成演示

先完成依赖配置和数据准备。下面每条命令都会真正调用你配置的模型服务，可能产生服务费用。

### 第一步：分析 A，生成图

```bash
npm run demo -- --case A --style default --output demo-A-v1
```

预期过程：加载 `ocean-sst-analysis` → 调用 `sst_analyze` → 检查数据 → 计算 → 生成图表与说明。

到 `outputs/demo-A-v1/` 打开 `sst_analysis.png` 查看图片，也有 SVG 和 PDF。图包含：

- 上面：2025 年每月海温与基准期同月平均的对照。
- 下面：1982–2025 年每个月相对同月基准的海温异常。

当前工具返回本地图片路径；可用文件管理器或图片查看器打开，不承诺网页端会自动嵌入图片。

### 第二步：修改 Skill，再画一次

```bash
npm run demo -- --set-style presentation --case A --output demo-A-v2
```

这会要求模型调用 `sst_set_style`，实际修改 `SKILL.md` 中的默认样式，再重新加载 Skill、运行分析。`presentation` 使用更大的字体、正负异常分色；数据和计算公式不变。

注意：`--style` 只指定本次样式；`--set-style` 修改保存下来的 Skill 默认值，两者不能同时使用。

要实际核对两次数值表是否相同：

```bash
npm run results:compare -- outputs/demo-A-v1 outputs/demo-A-v2
```

这一步是本地文件比较，不请求模型。只有比较实际通过后，才可说“已验证修改样式没有改变数值”。

### 第三步：新任务换成 B

```bash
npm run demo -- --case B --output demo-B-v1
```

每次 headless 命令会新建任务。这里省略 `--style`，使用上一步保存的 Skill 默认样式；工具会为 B 重新计算气候态，不沿用 A 的结果。

### 第四步：故意给缺月数据

```bash
npm run demo -- --case missing-month --output demo-gap-v1
```

应准确报告缺少 `2000-02`、二月基准样本不足 30 个，并停止分析。**此步骤预期不生成科学图片。** Python 分析工具以退出码 2 表示数据不合格；Harness 外层若成功验证了这个预期错误，可以将异常教学流程视为完成。

每次 `--output` 都需要一个新目录名；已有非空目录会被拒绝，避免旧图冒充本次结果。重复演示时将 `v1` 改成 `v2` 或其他新名称即可。

## 6. 用网页对话演示

```bash
npm run web -- --port 3080
```

打开终端显示的本地地址，输入：

> 加载 ocean-sst-analysis，分析 A 数据，以 1991–2020 为基准，输出到 web-A-01。展示结果路径，解释一个月份的距平计算；检查失败就停止。

然后可以继续：

> 把 Skill 默认样式改成 presentation，重新加载 Skill，再分析 A，输出到 web-A-02。不要改变算法。

新建会话后输入：

> 使用 ocean-sst-analysis 分析 B，采用 Skill 默认样式，输出到 web-B-01。

> 下面是故意缺月的教学数据。用同一个 Skill 分析 missing-month，输出到 web-gap-01；指出检查到的问题，不补值、不换数据。

网页仅监听本机 `127.0.0.1`。按 Ctrl-C 结束。更完整提示见 [演示提示](skills/ocean-sst-analysis/references/demo-prompts.md)。

## 7. 生成哪些文件，怎么验收

| 输出 | 用途 |
| --- | --- |
| `sst_analysis.png` | 直接展示图片 |
| `sst_analysis.svg` / `.pdf` | 后续编辑或放进报告 |
| `result.csv` | 每个月的 SST、同月气候态和异常值 |
| `climatology.csv` | 12 个月的气候态及样本数 |
| `report.json` | 成功/失败、输入与脚本校验值、参数、来源和数值抽查 |
| `summary.md` | 中文结果说明、图注和解释范围 |
| `outputs/harness-evidence/` | Harness 工具调用、日志及流程是否实际完成的记录 |

验收应看工具实际调用和 `report.json`，不能仅凭助手说“已完成”。同月气候态是基准期 30 个相同月份月均值的平均；月异常 = 当月海温 − 对应月份气候态。脚本要求月份无重复、日覆盖完整、基准期完整、单位明确。

本示例不计算趋势显著性，不判断海洋热浪，不作因果归因。原始数据和科学解释仍需研究者核对。

## 8. 把 Skill 带走复用

Skill 已保存在 `skills/ocean-sst-analysis/`，包含规则、分析脚本和方法说明。导出：

```bash
npm run skill:export
```

得到 `dist/ocean-sst-analysis.zip`。它不包含 key、真实数据或运行结果。再次导出时指定新文件名，例如：

```bash
npm run skill:export -- --output dist/ocean-sst-analysis-v2.zip
```

**换设备最简单的方式是克隆整个仓库**，重新安装依赖、填写本地 `.env`、准备数据，之后按第 5 节运行。如果想沿用现场修改过的 Skill，可将导出的包解压到新项目的 `skills/`，检查并保留原版本后替换。

在其他 Harness 项目里仅复制 `SKILL.md` 不会自动注册计算工具。还需要本项目的工具插件、Harness 配置和相应依赖；当前专用工具只支持 A、B、missing-month 三个案例。支持你自己的新数据时，需按字段契约准备 CSV/metadata，并调整 `harness/ocean-tools.mjs` 的案例映射。

## 9. 本地检查与备用出图

这些命令供之后自行验收，不调用模型 API：

```bash
npm run test:analysis
npm run test:harness
npm run plot:A
npm run plot:B
npm run plot:gap
```

`plot:*` 直接运行同一 Python 工具，可用于区分“计算问题”和“模型/代理问题”，不等于已经完成 Harness 演示。`plot:gap` 返回错误是预期行为。测试中的人工数值只用于验证公式，不作为科学样例数据。

## 10. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 提示缺模型名或 key | 填写项目根目录 `.env`，不是编辑 Skill |
| 401 / 403 | 在服务控制台核对 key、额度或模型权限；不要把 key 贴到公开 issue |
| 404 或模型不存在 | 核对完整 base URL（包括是否需要 `/v1`）和准确模型 ID |
| 返回普通聊天但不调用工具 | 确认模型和代理支持 tool calls；检查 `harness-evidence`，不要把文本回答算成功 |
| 缺 CSV | 先完成 `npm run data:prepare`，且确认它成功退出 |
| NOAA 超时或镜像缺项 | 重跑以续用缓存，降低 workers 或增大 timeout；保留失败报告，不自行补数值 |
| Python 依赖缺失 | 使用 `.venv` 安装 `requirements.txt`，或设置 `SCI_DSH_PYTHON` |
| 输出目录非空 | 换一个新的 `--output` 名称 |
| 默认样式没有变化 | 区分 `--style` 和 `--set-style`，检查 Skill 默认行并重新加载 |

## 11. 代码位置

```text
scripts/fetch_noaa_oisst.py                   下载、聚合、来源记录、缺月教学样例
scripts/run-harness.mjs                      启动真正的 DeepSeek Harness
scripts/local-step.mjs                       本地操作入口
scripts/compare_results.py                  样式修改前后的数值比较
scripts/export_skill.py                     导出 Skill
harness/sci.patch.yml                       模型、Skill 和工具配置
harness/sci.web.patch.yml                   网页模式配置
harness/ocean-tools.mjs                     sst_analyze / sst_set_style 工具
skills/ocean-sst-analysis/SKILL.md           可复用操作规则
skills/ocean-sst-analysis/scripts/analyze_sst.py
                                            数据质控、计算、绘图
tests/                                     供之后主动运行的检查
```

源码中没有自动定时任务，也没有提交后自动运行的 CI。数据下载、API 调用、图片生成和测试都由使用者明确执行相应命令触发。
