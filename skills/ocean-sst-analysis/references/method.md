# 方法与验收

此文件供讲者和维护者在本地编辑器中查看。当前 DeepSeek Harness 通过 `skill` 加载正文，通过 `sst_analyze` 执行分析，通过 `sst_set_style` 修改默认样式；没有任意读取文件或执行 shell 的工具。必要科学规则已放入 `SKILL.md`，不能让 Agent 依赖读取本文件才能完成任务。

## 专用工具契约

- `sst_analyze` 接收 `case`（`A`、`B`、`missing-month`）、可选 `style`（`default`、`presentation`）、`baseline_start`、`baseline_end`、`output`。`output` 是 `outputs/` 下简单子目录名，不是任意路径。
- 省略 `style` 时，工具从 `SKILL.md` 的独占行 `默认演示样式：default` 或 `默认演示样式：presentation` 读取默认值；显式传入只覆盖本次运行。
- `sst_set_style` 接收 `style`，只修改该独占行，并返回真实修改前后值、文件校验值和重新加载提示。它不修改科学算法或数据。
- 分析返回完整 `report`、脚本标准输出摘要、`exitCode` 和本次产物。Agent 用返回值说明检查和计算，不声称读取了工具未提供的 CSV。仅出现图路径也不能替代成功状态。
- 当前没有完成两次 CSV 逐值比较的专用工具。样式不应影响数值是实现约定，不能把这个约定写成已经完成独立对照验证。

## 分析对象与来源

输入是 NOAA 海表温度产品中**一个固定格点**的月均数据。日值获取与月均聚合在演示前完成，本脚本分析提供的月均 CSV；不能描述为现场重新下载或重新计算了全部日值。

产品、坐标、覆盖时间和来源以配套 metadata 及实际文件为准。必须区分原始产品与本演示制作的月均派生表。`sha256` 用于标识配套 CSV 的内容；不要只凭相似文件名配对。

## 输入约定

CSV 列：

| 列 | 含义 |
|---|---|
| `month` | `YYYY-MM` 格式的月份 |
| `lat`、`lon` | 单一固定格点的纬度、经度 |
| `sst_mean_c` | 预先聚合的月均 SST，单位摄氏度 |
| `n_valid_days` | 该月参与聚合的有效日数 |
| `n_expected_days` | 该自然月应有日数 |

配套 metadata 包含 `units`、`lat`、`lon`、`start_month`、`end_month`、`source_url`、`source_title`、`product`、`retrieved_at`、`sha256`。单位必须明确为 `degree_Celsius`；不能仅从列名、数值量级或另一份文件推断。

脚本是实际质控的唯一执行入口。它严格校验 CSV 的 SHA-256 与 metadata 是否匹配，并拒绝重复月份、声明时间范围内缺月、单位不明确或格点不一致等输入。本演示要求完整日覆盖：`n_expected_days` 应等于该自然月天数，`n_valid_days` 应等于 `n_expected_days`；不接受只覆盖部分日期的月均值。不能在失败后悄悄降低规则。

## 计算定义

默认基准期为 **1991–2020**，两端均包含，共 30 年。本演示要求连续、完整的 30 年基准期；不足 30 年或缺少任何基准月均不接受。每个历月均有 30 个有效月均值，输出中的 `sample_count` 应为 30。

对于历月 `m`，同月气候态为基准期内该历月月均 SST 的算术平均：

```text
C[m] = mean(SST[y, m]，y 为完整基准期中的年份)
anomaly[t] = SST[t] - C[month(t)]
```

同月气候态是 12 个数，不是把所有月份混合得到的一个总平均。距平的差值单位记为 °C。月均值已经由日值聚合完成，不在此脚本中按 `n_valid_days` 再次加权。

变更基准期属于分析方法变更。例如 `1982–2011` 只有在输入覆盖该完整 30 年且脚本通过校验时才能使用。变更后 SST 原值保持不变，气候态与距平需要重新计算，不能把曲线平移解释为海温观测本身发生变化。

## 输出及验收

| 输出 | 用途与验收 |
|---|---|
| PNG、SVG 图 | 视觉查看与后续编辑；应标明格点、单位和基准期 |
| `result.csv` | 逐月结果；抽查 SST、对应历月气候态和距平的关系 |
| `climatology.csv` | 12 个历月气候态；核对样本数与完整基准期 |
| `report.json` | 本次输入、参数与检查记录；用于确认本次运行成功 |
| `summary.md` | 可读摘要与来源；不得超出实际计算结果 |

接受成功需要同时满足：脚本成功退出、上述输出来自本次运行、图可打开、样本月公式核对一致。文件存在本身不证明本次任务成功。每次使用不存在或为空的输出目录；脚本拒绝向非空目录写入。输入校验失败时退出码为 `2`，新输出目录中的 `report.json` 标记 `status: error`，不产生科学结果。

`default` 与 `presentation` 仅改变视觉样式。同输入、同基准期下，两种样式的结果表和气候态表必须一致。具体可用样式由脚本实现，不由模型临时重写计算逻辑。迁移时须一并复制脚本实际依赖的资源。

## 异常演练

预先向观众说明 `point_B_gap_demo` 是人工删去 **2000-02** 的教学副本。制作副本时须同步更新其 metadata 中的文件 SHA-256，并标记为教学数据；声明时间范围仍覆盖删去的月份，使缺月检查能够检出异常。该月处于默认基准期内，因此不能仅用其他月份继续得到合格的 30 年同月气候态。

演练任务只选择 `case: missing-month`，其输入与配套 metadata 由专用工具映射。正常 B 属于另一 case，不能自动切换或借用其结果。预期结果是停止并指出缺失月份，而不是出图。不得为完成演示而插值、缩短基准期或重新抓取数据。

若检查未能发现异常，记录为未通过，保留输入与日志以定位原因；不要将其包装为成功。正常新格点复用可在另一个独立任务中使用 `point_B_monthly.csv` 与 `point_B.metadata.json` 验证。

## 结论边界

本流程不进行显著性检验、长期趋势估计、因果归因或海洋热浪事件识别。月均正距平不等于按日阈值和持续时间定义的海洋热浪；一个格点不能代表南海空间平均。数据预处理、科学方法选定和结果解释仍需要研究者核查。

## 本地备用命令与迁移

下面是供讲者之后使用的本地备用命令，不是当前 Harness Agent 可调用的工具，也不表示已经运行。进入项目根目录，并选择已安装项目 `requirements.txt` 的 Python 环境后：

```bash
python skills/ocean-sst-analysis/scripts/analyze_sst.py \
  --input data/point_A_monthly.csv \
  --metadata data/point_A.metadata.json \
  --baseline 1991 2020 \
  --output outputs/manual-A-default \
  --style default
```

分析脚本依赖 NumPy 和 Matplotlib；依赖约束见项目根目录 `requirements.txt`。每次运行使用新输出目录。独立比较样式前后的 `result.csv` 与 `climatology.csv`，只能在实际完成比较后报告通过；本文不预先宣称验证结果。

迁移专用工具演示应携带：完整 Skill 目录、`harness/ocean-tools.mjs` 及项目 Harness 配置和运行入口、与所选 case 配套的数据及 metadata、`package.json`/锁文件和 Python 依赖说明。单独复制 `SKILL.md` 不会自动注册 `sst_analyze` 或 `sst_set_style`。推荐迁移完整项目，保留相对目录结构。认证通过运行环境或 Harness 已有配置提供，不把用户 API key 写入 Skill、示例或项目文件。
