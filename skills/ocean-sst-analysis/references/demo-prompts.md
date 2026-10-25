# 三条现场演示提示

供讲者查看，不要求 Agent 用未提供的读取工具访问本文件。DeepSeek Harness 提供 `skill`、`sst_analyze`、`sst_set_style` 专用工具；数据、脚本与工具接入预先准备。现场展示执行、规则修改和新任务复用，不宣称从零开发全部分析代码。

以下提示属于后续演示流程，写入文件不代表已执行或通过验收。

## 1. 首次分析与方法检查

> 加载 ocean-sst-analysis。用 sst_analyze 实际检查并分析 A 数据，基准期 1991–2020，本次使用 default 样式，输出目录名 demo-A-default。成功后给出图和摘要路径，按返回报告展示来源、格点、时间范围与一个月份的距平计算；失败则停止并报告原因。

预期调用参数：

```json
{"case":"A","style":"default","baseline_start":1991,"baseline_end":2020,"output":"demo-A-default"}
```

讲者检查：工具实际调用、返回退出码与报告状态、来源与参数、一个月份的算术关系。此步骤尚不证明模型独立复算了全部结果。

## 2. 修改 Skill 的默认样式，再运行

> 用 sst_set_style 将 ocean-sst-analysis 的默认演示样式改为 presentation，展示真实修改结果并重新加载 Skill。随后用 A 数据、1991–2020 基准期运行到 demo-A-presentation；这次不要显式传入 style，让工具读取 Skill 的新默认值。给出前后图的路径及两次数值抽查，说明当前工具尚未进行两份 CSV 的独立逐值比较。

预期先调用 `sst_set_style`：

```json
{"style":"presentation"}
```

重新加载 Skill 后，再调用 `sst_analyze`：

```json
{"case":"A","baseline_start":1991,"baseline_end":2020,"output":"demo-A-presentation"}
```

讲者检查：默认样式行确实落盘；工具返回修改前后值与校验值；分析报告的样式为 `presentation`。本轮不改基准期、不改分析脚本。每次重新演示需换用空输出目录名。

## 3. 新任务导入后的异常输入演练

将完整 Skill 与配套专用工具迁移到新项目，确认注册成功，再开新任务。提前告诉观众：`missing-month` 为故意删去基准期内 `2000-02` 的教学副本。新任务只请求这组数据，不让 Agent 用正常 B 结果代替。

> 加载导入的 ocean-sst-analysis，用 sst_analyze 检查并分析 missing-month 数据，基准期 1991–2020，输出目录名 demo-B-gap。必须实际调用验证工具；输入未通过时指出具体问题并停止，不补值、不缩短基准期、不切换数据，不引用其他任务的图。

预期调用参数：

```json
{"case":"missing-month","baseline_start":1991,"baseline_end":2020,"output":"demo-B-gap"}
```

讲者检查：Skill 被发现并加载，工具报告缺失月份，Agent 停止，返回的科学产物为空。不能只复述“故意删月”的提示就声称完成校验。

正常新格点复用可在另一个独立任务中复用提示 1，将 A 改为 B，使用新的输出目录。若要验证默认规则继承，省略样式要求，不重新粘贴整套方法。

现场响应或网络失败时，可展示同版本输入、Skill 与工具的预存执行记录，并明确标为备用回放。当前未执行的流程不得标记为已成功验证。
