# 方法与验收

此文件供讲者与维护者查看。安装依赖、填写 API 配置、启动 DeepSeek Harness 之后，数据准备、分析、图像展示、结果比较，以及 Skill 的创建、修改、导入和导出均可在 Harness 对话内完成。本文描述待执行流程，不代表这些步骤已经运行或通过验收。

初始 `skills/` 没有专用 Skill。`skill-templates/ocean-sst-analysis/` 提供科学方法模板及分析脚本。先用基础工具完成分析，再将模板与成功证据生成草稿、导出、导入和加载；这是可追溯的工作流封装，不是模型自动发现算法。

## 工具契约

| 工具 | 输入与作用 |
|---|---|
| `sst_status` | 无参数，报告数据文件、安装、草稿和成功运行的本地状态；文件存在不等于科学质控已通过 |
| `sst_prepare_data` | 可选 `workers`（1–4，默认 2）、`timeout_seconds`（30–120，默认 60）、`refresh`；仅按明确请求下载、校验和聚合 NOAA 数据 |
| `sst_analyze` | `case` 为 A/B/missing-month；可选 `style`、`baseline_start`、`baseline_end`、`output`；执行质控、计算与绘图 |
| `sst_show_figure` | `run` 为成功运行目录名；Web 模式提供内部图库链接，其他模式提供本地路径 |
| `sst_compare_results` | `first`、`second` 为成功运行目录名；`mode` 为 style/baseline，默认 style；实际比较结果表 |
| `sst_create_skill` | `run` 指向成功运行；使用模板及经过核对的运行证据创建草稿 |
| `sst_export_skill` | `source` 为 draft/installed（默认 installed），可选 `archive` 包名，不含 `.zip`；导出到 `dist/` |
| `sst_install_skill` | `source` 为 archive/draft（默认 archive）、可选 `archive` 包名及 `replace`；安装并检查是否已被 Harness 发现 |
| `skill` | 加载 `ocean-sst-analysis`；安装后、修改后及每次新任务重新加载 |
| `sst_set_style` | `style` 为 default/presentation；修改已安装 Skill 的默认样式，随后重新加载 |

分析与图像工具只接受三种项目案例及简单输出目录名，不接受任意文件路径。省略 `style` 时读取已安装 Skill 的默认样式行；首次未安装时读取模板默认值。显式 `style` 只作用于本次运行。项目不开放任意文件读取和 shell 工具，科学步骤不依赖 Agent 另行读取本参考文件。

## 数据与来源

输入是 NOAA OISST 分析产品中一个固定格点的月均 SST。A 为 18.125°N、114.125°E；B 为 15.125°N、114.125°E；每份覆盖 1982–2025，共 528 个月。它们不是观测站，也不是南海区域平均。

`sst_prepare_data` 内部按年获取日值，验证日期、坐标、单位后生成月均表与 metadata。成功缓存可续用；不能把续用缓存说成重新下载。历史镜像补充策略、实际来源文件与使用记录以下载脚本及 metadata 为准，不自行插值或制造缺失值。分析工具处理已聚合的月均输入，不重新计算日值。

CSV 字段：

| 列 | 含义 |
|---|---|
| `month` | `YYYY-MM` 格式的月份 |
| `lat`、`lon` | 单一固定格点的纬度、经度 |
| `sst_mean_c` | 月均 SST，单位摄氏度 |
| `n_valid_days` | 该月参与聚合的有效日数 |
| `n_expected_days` | 该自然月应有日数 |

metadata 包含 `units`、`lat`、`lon`、`start_month`、`end_month`、`source_url`、`source_title`、`product`、`retrieved_at`、`sha256`。单位必须明确为 `degree_Celsius`，不能从数值量级猜测。文件配对要验证 SHA-256，不能只看名字相似。

## 质量检查与计算

`sst_analyze` 是实际质控入口，拒绝重复月份、声明范围内缺月、非有限数值、单位或格点不一致，以及不完整覆盖。ERDDAP 日值路径要求 `n_expected_days` 等于自然月天数、`n_valid_days` 等于 `n_expected_days`，并在报告中标记 `complete_daily_coverage: true`；PSL 回退使用官方发布月平均，报告标记 `complete_source_coverage: true` 和 `complete_daily_coverage: false`，不能把它表述为本地逐日核验。不能为出图而放宽月份、来源或单位检查。

默认基准期 **1991–2020**，两端均包含。每个历月有 30 个完整月均值，输出 `sample_count` 为 30：

```text
C[m] = mean(SST[y, m]，y 为基准期内的年份)
anomaly[t] = SST[t] - C[month(t)]
```

气候态是 12 个数，不能用所有月份混合的一个平均值。日值已经聚合成月均；同月气候态等权平均 30 个年份的月均值，不按日数再次加权。分析还检查基准期内各历月平均距平接近零。

改为 **1982–2011** 等其他基准期时，必须覆盖完整连续 30 年。SST 原值不变，气候态和距平重新计算。`sst_compare_results(mode="baseline")` 核对输入及方法一致、SST 原值未变，并验证逐月恒等式：

```text
anomaly_first - anomaly_second = climatology_second - climatology_first
```

比较容差以脚本实现为准，当前最大绝对误差为 `1e-9 °C`。调整量可以随历月变化，不是统一常数。`same_baseline: true` 不能作为“已经更换基准期”的证据。

## 输出及图片

| 输出 | 用途与验收 |
|---|---|
| `sst_analysis.png` | 图库预览；上图为最后一年季节循环与同月气候态，下图为全部月份距平 |
| `sst_analysis.svg`、`.pdf` | 之后编辑或用于报告的矢量文件 |
| `result.csv` | 每月 SST、气候态和距平及覆盖日数 |
| `climatology.csv` | 12 个历月的气候态及样本数 |
| `report.json` | 本次状态、参数、输入/metadata/脚本校验值、完整来源与数值抽查 |
| `summary.md` | 中文结果说明与图注 |

成功需要真实退出码 `0`、`ok: true`、`report.status: ok` 和本次产物。工具传给模型的来源摘要会压缩冗长来源列表，完整记录保存在 `report.json`。数值抽查来自脚本返回，不称为模型独立复算。

成功后通过 `sst_show_figure` 返回的图库链接查看 PNG、摘要和下载项。图库仅展示成功报告声明的产物；链接存在不等于已经完成视觉验收，讲者需实际打开确认。没有启动 Web 时，工具只返回本地路径，不假装有可访问的图库。

每次使用新输出目录。非空目录会拒绝，避免旧图冒充本次结果。输入校验失败时退出码 `2`，报告 `status: error`、科学产物为空；报告和日志只用于诊断。

## 样式与比较

`default` 与 `presentation` 改变字体、颜色等视觉样式，不改变公式。修改保存的默认值需要 `sst_set_style`，随后重新加载 Skill、再次分析，并省略本次 `style` 参数。

`sst_compare_results(mode="style")` 核对输入、metadata、方法版本、脚本校验值、基准期一致，然后对 `result.csv` 和 `climatology.csv` 做字节比较。只有真实返回成功且两项 `identical: true` 时，才报告“已验证改样式没有改变数值”；不能用设计约定代替实际比较。

## Skill 的创建与迁移

创建要求一次真实成功分析，核对输入、metadata 与模板脚本的 SHA-256、必要产物、完整性检查和 30 年基准期。`references/example-run.json` 是精简的成功证据记录，不包含原设备的绝对输入路径，也不是独立复算的证明。

首次流程是：成功分析 → `sst_create_skill` → 从 draft 导出 → 从 archive 导入 → 确认 `ready` → 官方 `skill` 加载。已安装版本修改样式后从 installed 导出。包名不含 `.zip`；导出包不覆盖已有文件，已有安装默认不覆盖，明确更新时才 `replace: true` 并保留备份。

ZIP 根目录直接包含 `SKILL.md`、`scripts/`、`references/`。包不包含 key、原始数据或完整运行输出。它仍依赖项目工具插件和 Python 环境；只复制 `SKILL.md` 不会自动出现 `sst_analyze` 等工具。

换设备推荐克隆整个仓库、安装锁定依赖、配置自己的本地 `.env`，将 ZIP 放入新项目 `dist/`，启动 Harness 后请求导入并加载。数据可重新准备，也可携带完整已校验数据及配对 metadata；不得只搬图而声称重算完成。首次安装和 API 配置见项目 README。

## 缺月演练与结论边界

`point_B_gap_demo` 是人工删除 **2000-02** 的教学副本，metadata 的 SHA-256 与副本同步，声明时间范围仍包含缺失月。使用 `case: missing-month`，实际检查应发现该月缺失及基准期不足，随后停止，不出科学图。不能自动切换 B、插值或缩短基准期。

检查未发现预期异常就记为失败并保留证据。数据下载失败、模型未调用工具、比较失败或图片打不开，也均按真实状态说明，不能用预期效果冒充实际结果。

本流程不进行趋势显著性检验、因果归因或海洋热浪识别。月均正距平不等于日阈值及持续时间定义的海洋热浪。数据适用性、方法选择与结果解释仍由研究者核查。
