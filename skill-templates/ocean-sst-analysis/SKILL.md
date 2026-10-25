---
name: ocean-sst-analysis
description: 在 DeepSeek Harness 中分析 NOAA 单格点月均海温 A/B 数据，执行质控、同月气候态与距平计算、展示图片、修改图样式、比较基准期及缺月演练；配合项目工具创建、导入和导出可复用 Skill。不用于海区均温、因果归因或海洋热浪判定。
---

# 单格点海温分析

所有科研业务均通过本项目注册在 DeepSeek Harness 中的工具执行。计算与绘图由确定性脚本完成，模型负责选择步骤、调用工具、说明真实返回结果。当前只支持项目内的 `A`、`B`、`missing-month` 三组输入，不能声称已分析用户另行提供的任意文件。

默认演示样式：default

## 先确认状态

1. 调用 `sst_status`。它只说明本地文件是否存在，不等于数据已经通过科学质控。
2. 缺少数据且用户明确要求准备数据时，调用 `sst_prepare_data`。首次下载可能耗时数分钟；默认续用缓存，只有用户要求重新下载才传 `refresh: true`。准备失败就说明真实问题，不把部分文件当作完整数据。
3. 已安装本 Skill 时，先用官方 `skill` 工具加载 `ocean-sst-analysis`。初始项目只有方法模板、尚无已安装 Skill：允许先直接调用基础分析工具，完成一次成功分析后再创建 Skill；不得假称已经加载。

## 分析、展示与验收

调用 `sst_analyze`。A 为首次格点，B 为新任务复用格点，`missing-month` 为人工删去 `2000-02` 的教学副本。未指定时使用 A 和 **1991–2020** 基准期。输出使用 `outputs/` 下新的简单子目录名。

```json
{"case":"A","baseline_start":1991,"baseline_end":2020,"output":"demo-A-v1"}
```

省略 `style` 时，工具读取本 Skill 的默认样式；首次未安装时读取方法模板的默认值。用户只要求本次样式时才传 `default` 或 `presentation`，这不会修改已保存的默认值。

必须以实际返回判断完成：要求 `ok: true`、`exitCode: 0`、`report.status: ok`，且存在本次科学产物。根据 `report.source`、`point`、`coverage`、`baseline`、`checks` 和 `spot_check` 说明来源、格点、时间、基准期与数值抽查。不得把模型文字回答当作工具执行证据。

成功后调用 `sst_show_figure`：

```json
{"run":"demo-A-v1"}
```

Web 模式返回 Harness 内部图库链接，可预览 PNG、查看摘要及下载 PNG/SVG/PDF/CSV；向用户提供实际返回的链接。无 Web 服务时只返回本地文件路径及启动提示。不得声称自己已经打开或视觉核验了未实际查看的图，也不得为失败运行引用历史图。

失败立即停止科学解释。数据校验失败通常返回退出码 `2`、`status: error` 和空科学产物列表。指出具体问题，不自动删重、插值、补月、猜单位、缩短基准期或切换正常输入。非空输出目录遭拒绝时可选择新目录；输入未修正时不要重复运行同一失败分析。

## 核心科学方法

- 输入为固定格点的月均 SST。分析检查 CSV 与 metadata 的 SHA-256 配对、显式单位 `degree_Celsius`、格点一致、月份唯一且连续、自然月全部日期有效，以及完整连续的 30 年基准期。
- 每个历月的气候态，是基准期内该历月 30 个完整月均值的算术平均；月距平 = 当月 SST − 对应历月气候态。不能用所有月份混合的总平均替代同月气候态。
- `report.spot_check` 是脚本返回的单月数值抽查，不是模型独立读取 CSV 重算的证明。
- 改基准期只按用户要求执行，重新计算气候态与距平。SST 原值不变。一次只改变基准期或图样式，便于说明差异。

## 从成功分析创建、导入与导出

本演示以已编写的科学方法模板，加上一次真实成功运行的证据，形成可复用 Skill。不是训练模型，也不是从零自动发现科学方法。

1. `sst_create_skill({"run":"demo-A-v1"})`：核对成功报告、输入及脚本校验值，创建 `drafts/ocean-sst-analysis/`，记录可追溯的示例运行证据。失败记录不能创建 Skill；已有草稿不会覆盖。
2. `sst_export_skill({"source":"draft","archive":"ocean-sst-analysis"})`：生成 `dist/ocean-sst-analysis.zip`。包名不含扩展名；已有包不覆盖。
3. `sst_install_skill({"source":"archive","archive":"ocean-sst-analysis"})`：安装并检查 Harness 是否发现新版本。也可用 `source: "draft"` 直接安装草稿。只有返回 `ready: true` 后才继续加载；若仅文件安装成功而尚未发现，按返回提示刷新或新建会话。
4. 再调用官方 `skill` 加载同名 Skill，之后进行复用。文件安装和实际加载是两个步骤。
5. 修改已安装 Skill 后，使用 `sst_export_skill({"source":"installed","archive":"ocean-sst-analysis-v2"})` 导出修改版。只有用户明确要求更新已有安装时，才对 `sst_install_skill` 传 `replace: true`；工具会保留旧版备份。

ZIP 不携带 API key、原始海温数据或完整运行结果。换设备时推荐克隆完整项目、安装依赖、配置本地认证，把 ZIP 放入 `dist/` 后在 Harness 中导入。单独复制 `SKILL.md` 不会注册项目计算工具。

## 修改样式并实际比较

用户要求修改默认图样式时，调用 `sst_set_style({"style":"presentation"})`。工具修改已安装 Skill 的默认样式行，返回修改前后值与文件校验值，不改算法。完成后重新加载 Skill，再分析同一 A 数据到新目录 `demo-A-v2`，省略 `style` 以采用保存的默认值。

随后调用：

```json
{"first":"demo-A-v1","second":"demo-A-v2","mode":"style"}
```

此参数交给 `sst_compare_results`。工具核对输入、metadata、方法、脚本和基准期一致，并实际比较 `result.csv` 与 `climatology.csv`。只有真实返回成功、两表 `identical: true` 才能说已验证“改样式没有改变数值”。比较失败则如实报告，不以实现约定代替检查。使用 `sst_show_figure` 分别提供前后两张图。

## 新任务复用与基准期变化

新任务中重新加载本 Skill，分析 B 并采用保存的默认样式；B 的气候态必须重新计算，不能沿用 A 的数值。异常练习选择 `missing-month`，提前说明是教学副本，仍须实际调用工具检出 `2000-02` 缺失并停止，不生成科学图片。

用户要求改变基准期时，例如将 A 改为 **1982–2011**，保持同样式和新输出目录，再调用 `sst_compare_results`，使用 `mode: "baseline"` 对比前后结果。只有通过实际检查才说明 SST 原值未变，并且逐月满足：

```text
距平_旧 − 距平_新 = 同月气候态_新 − 同月气候态_旧
```

调整量可能因历月不同而不同，不能说成整条曲线统一平移一个常数。若返回 `same_baseline: true`，说明两次基准期相同，不得当作基准变更演示。

## 结果表述边界

- 描述输入格点与时间段，不把一个格点称为整个南海的平均值。
- 正距平仅表示相对选定同月气候态偏暖。本流程不做显著趋势检验、海洋热浪判定或因果归因。
- 只引用实际返回的来源、URL、检索时间和产品信息，不编造引用或处理步骤。区分 NOAA 分析产品与本演示的月均派生表。
- 研究者需核对方法与解释，不宣称完成自主科学发现。未运行的流程只能称为待验证，不能写成已通过。

讲者与维护者可在编辑器查看 [方法与验收](references/method.md) 和 [逐步演示提示](references/demo-prompts.md)。本项目不提供任意文件读取或 shell 工具；必要规则已写在正文，不要求 Agent 读取参考文件才能完成任务。
