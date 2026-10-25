# 当前状态与阻塞问题

更新时间：2026-10-25（Asia/Shanghai）

## 已完成

- 已在 Windows 本机完成仓库克隆、Node 依赖安装和 Python 虚拟环境配置。
- 已验证 DSH 版本 `0.1.0-rc.6`、工具包版本 `0.1.0-rc.7`。
- 已在本地 `.env` 配置代理地址和模型。API key 只保存在本机，被 `.gitignore` 忽略，没有提交到仓库。
- 已验证 `https://token.fncompute.com/v1` 的模型列表、流式输出和工具调用；当前本机使用的模型 ID 为 `deepseek-v4.1-flash-1017`。
- `npm run harness:check` 通过。
- Python 核心分析测试 10/10 通过；合并最新工作流提交后，Node 测试 15/15 通过。
- 完整 Python 测试当前为 35/38 通过，剩余 3 个失败都集中在 Windows 下 ZIP 测试的同一处：`zipfile` 会把测试输入的 `scripts\\escape.py` 规范化为 `scripts/escape.py`，导致该恶意路径样例没有按测试预期保留反斜杠；随后同一测试中的 `.env` 和 `scripts/__pycache__` 检查因目标目录已被前一个样例创建而连带失败。生产路径校验仍拒绝 `..`、绝对路径、隐藏文件和缓存目录。
- `npm run web -- --port 3080` 已启动，网页内测声明的确认状态可以保存，刷新后不会重复弹出。

## 当前阻塞

第 6 步网页数据准备未通过。网页中实际调用了 `sst_analyze`，但三个输入文件都不存在：

```text
data/point_A_monthly.csv
data/point_B_monthly.csv
data/point_B_gap_demo.csv
```

三次调用都返回退出码 `2` 和 `report.status: error`，只生成了诊断报告：

- `outputs/check-A/report.json`
- `outputs/check-B/report.json`
- `outputs/check-missing-month/report.json`

报告中的原始问题分别是：

```text
无法读取输入文件：[Errno 2] No such file or directory: data/point_A_monthly.csv
无法读取输入文件：[Errno 2] No such file or directory: data/point_B_monthly.csv
无法读取输入文件：[Errno 2] No such file or directory: data/point_B_gap_demo.csv
```

这意味着流程在“读取输入文件”阶段就结束了，还没有进入缺月、单位、日期连续性或 SHA-256 配对检查。每份报告的 `artifacts` 都是空数组，因此不能把这次运行当作出图成功。

此前运行 `npm run data:prepare -- --workers 2 --timeout 90` 时，NOAA ERDDAP 请求出现网络/SSL 连接失败，未生成官方数据文件。当前没有手写、插值或推算海温值。

网页端的模型在准备指令中并行发起了 A、B 和 `missing-month` 三次检查；三次都失败后停止，没有继续执行后续演示步骤。相关过程证据保存在 `outputs/harness-evidence/`，不提交到 Git。

## 尚未完成

由于数据准备失败，按演示要求没有继续发送第 7 步指令，因此以下结果尚未产生：

- A 的 `demo-A-v1` 图片
- 从 `demo-A-v1` 生成和导入 Skill 草稿
- `default` 与 `presentation` 的 A 图比较
- B 的复用分析图片
- `missing-month` 的缺少 `2000-02` 报告
- 本次流程最终导出的 Skill ZIP

## 继续条件

在能访问 NOAA ERDDAP 的网络环境中运行：

```bash
npm run data:prepare -- --workers 2 --timeout 90
```

确认 `data/` 中的三个 CSV 和对应 metadata 通过完整性校验后，再打开网页并从第 6 步重新开始。不要把 API key、真实数据或 `outputs/` 目录提交到 GitHub。
