# 当前状态与阻塞问题

更新时间：2026-10-25（Asia/Shanghai）

本文保留 Windows 设备在提交 `987104e` 时提供的运行记录。本次修复只作静态编辑和审查，未运行测试、下载数据、调用模型 API 或生成科学结果。历史通过数字不代表修复后的复测结果。

## 已完成的历史验证（987104e）

- 已在 Windows 本机完成仓库克隆、Node 依赖安装和 Python 虚拟环境配置。
- 已验证 DSH 版本 `0.1.0-rc.6`、工具包版本 `0.1.0-rc.7`。
- 已在本地 `.env` 配置代理地址和模型。API key 只保存在本机，被 `.gitignore` 忽略，没有提交到仓库。
- 已验证 `https://token.fncompute.com/v1` 的模型列表、流式输出和工具调用；当前本机使用的模型 ID 为 `deepseek-v4.1-flash-1017`。
- `npm run harness:check` 通过。
- Python 核心分析测试 10/10 通过；合并最新工作流提交后，Node 测试 15/15 通过。
- 完整 Python 测试当前为 35/38 通过，剩余 3 个失败都集中在 Windows 下 ZIP 测试的同一处：`zipfile` 会把测试输入的 `scripts\\escape.py` 规范化为 `scripts/escape.py`，导致该恶意路径样例没有按测试预期保留反斜杠；随后同一测试中的 `.env` 和 `scripts/__pycache__` 检查因目标目录已被前一个样例创建而连带失败。生产路径校验仍拒绝 `..`、绝对路径、隐藏文件和缓存目录。
- `npm run web -- --port 3080` 已启动，网页内测声明的确认状态可以保存，刷新后不会重复弹出。

## 上次运行的阻塞记录

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

## 本次修复（代码已修改，尚未运行复测）

- 下载 TLS 保留系统默认信任，追加 certifi；支持数据专用 HTTP/HTTPS 代理与机构 PEM CA，仍强制证书验证。
- 对网络、SSL、代理认证、HTTP 状态和数据格式错误分类；只对暂时性故障有限重试，不把故障页当成 CSV。
- 首个待获取年份未成功时，停止批量下载；后续失败保留已完成年度缓存，及时保存状态与安全诊断。时间预算早于 Harness 工具超时；系统 DNS 行为仍可能影响实际退出时间。
- 支持完整本地月数据直接复核复用，以及 `offline=true` 从完整本地数据或年度缓存准备；缺数据时不制造样例值。
- DSH 明确区分准备与分析：缺输入时返回 `DATA_NOT_PREPARED` 和下一步工具；数据准备与分析互斥，避免并行操作把半成品当作可用数据。
- 取消或进程等待失败时尽量保存已收集日志；下载子进程仅接收必要网络设置，不将代理凭据放入模型参数或诊断。
- Windows ZIP 测试保留真实原始危险文件名、隔离各样例；生产导入先检查 `orig_filename`，再检查规范化文件名一致性，防止反斜杠/NUL 在 Windows 上被隐藏。
- 补充网络、准备阶段、缺输入和 Windows ZIP 相关回归用例，但**没有执行新增或现有测试**。

没有实际重新下载验证，所以不能声称 NOAA 网络已经恢复，也不能把 Issue #1 视为完成端到端验收。图片、样式比较和 Skill ZIP 仍待真实数据准备成功后生成。

## 换设备更新与继续操作

在项目根目录更新代码，重新启动 Harness：

```bash
git pull --ff-only
npm run web
```

在网页输入：“调用 sst_prepare_data，workers=1，timeout_seconds=90。失败时展示 error.category、message 和 action，不调用分析。”

如果仍是网络/SSL 失败，按 README 配置本地 `SCI_DSH_DATA_PROXY` 或机构 `SCI_DSH_DATA_CA_BUNDLE` 并重启；也可复制另一台已准备成功的电脑的完整 `data/` 后，请求 `offline=true`。API 网关不是数据下载代理。

完整性通过后再继续分析 A、创建 Skill、修改与比较、复用 B 和缺月教学。缺文件不等于检出了 `2000-02` 缺月。后续自行执行 `npm run test:analysis` 和 `npm run test:harness` 才能取得本次复测数字。不要提交 API key、代理凭据、真实数据或运行输出。
