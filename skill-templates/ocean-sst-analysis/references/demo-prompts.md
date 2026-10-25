# 逐步现场演示提示

先按项目 README 安装依赖、填写本地 API 配置并启动 DeepSeek Harness Web。以下文字逐条发送到 Harness 对话即可；每步等工具返回再继续。它们是待执行提示，写入文档不表示已经运行或验证成功。

初始项目只有预编写的科学方法模板，`skills/` 尚无专用 Skill。演示重点是：先完成一次分析，再把模板和成功证据生成可复用 Skill、导入后修改并复用。不要把这个过程说成模型从零发现了算法。

## 1. 查看准备状态

> 用 sst_status 检查海温数据、Skill 和已有结果的状态。只检查，不下载，不分析。如果还没有 Skill，说明我们会先用基础工具完成一次分析，再生成 Skill。

展示：工具实际列出的数据和安装状态。文件存在不等于科学检查已通过。

## 2. 准备真实数据

> 我现在明确要求准备数据。用 sst_prepare_data 下载并准备 A、B 两份 NOAA 海温数据及缺月教学副本，workers 为 2，timeout_seconds 为 60，续用已有缓存。报告是否得到两份各 528 个月的完整月均表；失败则说明原因并停止。

展示：真实数据来源、两个格点、覆盖期、完整性结果。首次下载建议在演讲前通过同一 Harness 流程完成；现场可展示已缓存数据与准备记录。不要声称缓存数据是现场重新下载。

## 3. 首次分析并展示图片

> 分析 A，基准期 1991–2020，本次使用 default 样式，输出到 demo-A-v1。如果已安装 ocean-sst-analysis，先加载它；如果尚未安装，直接使用项目基础分析工具。实际检查数据并计算；成功后用 sst_show_figure 展示本次图片链接，说明来源、格点和一个月份的距平计算；失败就停止。

关键工具参数：

```json
{"case":"A","style":"default","baseline_start":1991,"baseline_end":2020,"output":"demo-A-v1"}
```

展示：`sst_analyze` 返回成功，随后 `sst_show_figure({"run":"demo-A-v1"})` 返回图库链接。讲者打开链接展示 PNG；上图比较 2025 年季节循环与气候态，下图展示 1982–2025 的月距平。返回的单月抽查是脚本结果，不称为模型独立重算。

## 4. 把成功流程生成 Skill 草稿

> 用 sst_create_skill 从 demo-A-v1 创建 ocean-sst-analysis 草稿。说明生成的是已有科学方法模板、脚本和这次成功运行的证据；不是训练模型，也不是自动发现算法。只有工具真实返回成功才报告已创建。

关键参数：`{"run":"demo-A-v1"}`。

展示：草稿位置、文件数、示例运行证据路径。可由讲者在编辑器中查看 `drafts/ocean-sst-analysis/SKILL.md` 与 `references/example-run.json`；不要声称 Agent 使用了未提供的读文件工具。

## 5. 导出、导入并加载

> 先用 sst_export_skill 导出 source 为 draft、archive 为 ocean-sst-analysis；成功后用 sst_install_skill 从 archive 导入同一个包，不覆盖已有安装。确认 ready 为 true，再调用官方 skill 工具加载 ocean-sst-analysis。若尚未被当前会话发现，明确提示刷新或新建会话，不假装已加载。

关键参数依次为：

```json
{"source":"draft","archive":"ocean-sst-analysis"}
{"source":"archive","archive":"ocean-sst-analysis"}
```

展示：`dist/ocean-sst-analysis.zip`、真实安装结果、Harness 的发现和加载记录。包名参数没有 `.zip` 后缀。文件安装与实际加载分别展示。

## 6. 修改 Skill 默认样式，再出图并比较

> 用 sst_set_style 把已安装 Skill 的默认样式改成 presentation，展示修改前后值，再重新加载 Skill。以 A 和 1991–2020 基准期分析到 demo-A-v2，省略 style，让工具读取修改后的默认值。成功后展示新图，并用 sst_compare_results 比较 demo-A-v1 与 demo-A-v2，mode 为 style。只有真实比较通过才能说数值表没有变化。

关键顺序：修改 → 重新加载 → 再分析 → 展示 → 比较。

```json
{"style":"presentation"}
{"case":"A","baseline_start":1991,"baseline_end":2020,"output":"demo-A-v2"}
{"run":"demo-A-v2"}
{"first":"demo-A-v1","second":"demo-A-v2","mode":"style"}
```

展示：默认样式确实修改；字体与异常分色变化；比较返回两份 CSV 的真实一致结果。本轮不改基准期或计算脚本。

## 7. 新任务换格点复用

在 Harness 中新建会话，再输入：

> 加载 ocean-sst-analysis，分析 B，基准期 1991–2020，输出到 demo-B-v1。省略 style，沿用 Skill 保存的默认样式；为 B 重新计算自己的气候态。成功后用 sst_show_figure 展示本次图和数值抽查。

展示：只需加载 Skill 和简短任务就能处理另一格点；输出样式继承 `presentation`，数值来自 B 的新计算。不能沿用 A 的气候态。

## 8. 故意给缺月数据

> 下面使用人为删去 2000-02 的教学副本。加载 ocean-sst-analysis，用 sst_analyze 检查 missing-month，基准期 1991–2020，输出到 demo-gap-v1。必须实际调用检查；发现问题后指出具体月份并停止，不补值、不缩短基准期、不换成正常 B，也不展示历史图片。

关键参数：

```json
{"case":"missing-month","baseline_start":1991,"baseline_end":2020,"output":"demo-gap-v1"}
```

展示：实际错误报告、`2000-02` 缺失、科学产物为空。此步预期不出图；复述提示中的“缺月”不等于工具已检出。

## 9. 可选：改变基准期，再比较

> 加载 ocean-sst-analysis，保持当前 presentation 默认样式，分析 A，但基准期改为 1982–2011，输出到 demo-A-baseline。成功后展示图片，用 sst_compare_results 以 baseline 模式比较 demo-A-v2 与 demo-A-baseline。根据真实返回解释 SST 原值是否不变、同月气候态与距平如何调整，不把历月变化说成一个统一常数。

关键参数：

```json
{"case":"A","baseline_start":1982,"baseline_end":2011,"output":"demo-A-baseline"}
{"first":"demo-A-v2","second":"demo-A-baseline","mode":"baseline"}
```

展示：基准期标签改变；原始 SST 不变；不同历月的距平变化符合实际比较返回的恒等式。比较未通过就报告失败；两次基准相同不算改基准期演示。

## 10. 导出修改后的版本，换设备复用

> 用 sst_export_skill 导出已安装版本，source 为 installed，archive 为 ocean-sst-analysis-presentation。报告生成包的位置；说明包中不含 API key、原始海温数据和完整运行结果。

换设备时，先克隆整个项目、安装依赖并填写自己的 API 配置，将这个 ZIP 放到 `dist/`，启动 Harness 后输入：

> 从 archive 导入 ocean-sst-analysis-presentation，不覆盖已有安装。确认已经发现，再加载 ocean-sst-analysis，并用 sst_status 检查数据准备状态。

若用户明确要替换已安装版本，才增加 `replace: true`，并展示旧版本备份位置。单独复制 Skill 不会自动安装项目依赖或注册工具。

## 重复演示与失败处理

每轮换用新输出名与新导出包名；已有非空输出、草稿和同名 ZIP 默认不会覆盖。需要从第一步重新排练时，优先使用新的完整项目副本，避免把上轮已安装状态当成首次创建。

现场网络或 API 失败可展示事先真实运行的同版本记录，并明确说是备用回放。当前尚未运行的步骤只能称为计划或待验证，不可标记为已经成功。
