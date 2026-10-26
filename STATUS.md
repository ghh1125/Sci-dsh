# 当前状态与验收记录

更新时间：2026-10-26（Asia/Shanghai）

## 结论

Sci-dsh 已在 Windows 本机完成 DSH 网页端到端验收。A、B 使用 NOAA PSL 官方 OISST v2 highres 已发布月平均场的真实 528 个月数据；缺月教学副本实际删除了 `2000-02`。网页中已完成：

- 数据准备和 A 点 `demo-A-v1` 出图；
- 从 `demo-A-v1` 创建 `ocean-sst-analysis` 草稿；
- 导出草稿、从 ZIP 导入、确认 `ready=true` 并加载 Skill；
- 将默认样式改为 `presentation`，重新分析 A 为 `demo-A-v2`，展示两张图并按 `style` 比较；
- 新建会话加载 Skill，分析 B 为 `demo-B-v1` 并展示图片；
- 分析 `missing-month`，在校验阶段停止并报告缺少 `2000-02`，未补值、未引用旧图、未出图；
- 从已安装的 presentation 版本导出 `dist/ocean-sst-analysis-presentation.zip`。

本次复测还修正了一个科学语义问题：PSL 回退路径以前会把“528 个月完整”同时报告成 `complete_daily_coverage=true`。现在月产品报告为 `complete_monthly_coverage=true`、`complete_source_coverage=true`、`complete_daily_coverage=false`，并明确说明没有在本地核验逐日文件或重新聚合。ERDDAP 逐日路径仍可报告逐日覆盖。比较工具和 Skill 生命周期校验已同时接受这两种有证据的覆盖路径。

## 数据来源和网络处理

原下载器访问 NOAA ERDDAP 时实际返回了 TLS/网络错误。直连 `upwell.pfeg.noaa.gov` 还返回了 IP 黑名单页面，系统代理到 NOAA 的 TLS 握手也失败，因此没有把错误页当作 CSV，也没有生成模拟值。

本机 `.env` 使用了：

```dotenv
SCI_DSH_DATA_SOURCE=psl-monthly
SCI_DSH_DATA_PROXY=direct
```

新增的 `scripts/fetch_noaa_psl_monthly.py` 通过 NOAA PSL 官方 OPeNDAP 月平均数据集按点提取 A/B 两个格点，校验坐标、时间坐标、528 个月和海温范围，保存原始响应、来源 JSON、CSV、metadata、完整性报告和缺月副本。metadata 明确记录 `source_frequency=monthly_published`、未下载逐日文件、未在本地重新平均或插值。

## 网页中遇到并修复的错误

1. **API 401：无效令牌。** 本地 `.env` 的 API key 行曾把字面量 `` `r`nSCI_DSH_DATA_SOURCE...`` 拼在密钥后面，导致服务端将整串当作 key。已重写本机 `.env` 为独立配置行；随后 `/v1/models` 返回 200，模型为 `deepseek-v4.1-flash-1017`。密钥未写入仓库、日志或本文。
2. **Skill 创建校验失败。** Windows CRLF 使 `SKILL.md` 的 `name` 正则匹配失败，`sst_create_skill` 返回退出码 2。已让 frontmatter 校验同时接受 LF 和 CRLF，网页重试后创建成功。
3. **导入同名冲突。** 早期失败留下了只有 `__pycache__` 的不完整 `skills/ocean-sst-analysis` 目录；`sst_status` 因缺少 `SKILL.md` 显示未安装，而安装器按目录存在拒绝覆盖。已将不完整目录移入 `backups/`，重新从 ZIP 导入后 `ready=true`，Skill 可加载。
4. **最终 ZIP 同名冲突。** `dist/ocean-sst-analysis-presentation.zip` 原来是旧的 default 版本，工具按规则拒绝覆盖。已将旧包移入 `backups/`，再从当前 installed presentation 版本按原包名导出成功；本次复测网页返回 SHA-256 `d511121297fddebb11336028d86b0702fe0c250db22877b2edfa9a05efd7c3ec`。

## 产物

运行输出和数据默认被 `.gitignore` 忽略，留在本机：

- `outputs/demo-A-v1/`、`outputs/demo-A-v2/`、`outputs/demo-B-v1/`：PNG、SVG、PDF、CSV、报告；
- `outputs/demo-gap-v1/report.json`：退出码 2，实际报告缺少 `2000-02`、527/528 行、二月基准样本 29/30；
- `dist/ocean-sst-analysis.zip`：草稿导入用包；
- `dist/ocean-sst-analysis-presentation.zip`：最终 installed presentation Skill 包；
- `data/`：A、B、缺月副本、metadata、来源和完整性报告。

ZIP 只包含方法、脚本和必要参考，不包含 API key、原始海温数据或完整运行输出。

## 复测结果

已执行：

```text
Node: 20/20 tests passed
Python: 70 tests passed
Python compile: fetch_noaa_psl_monthly.py, compare_results.py, skill_lifecycle.py, analyze_sst.py passed
Harness check: harness 0.1.0-rc.6, tools 0.1.0-rc.7, skillInstalled=true, credentialPresent=true, modelPresent=true
```

Node 测试通过的是仓库当前测试集合；Python 测试覆盖下载网络、数据准备、分析和 Skill 生命周期。网页端的每一步均以实际工具返回为准，没有把模型思考或路径存在当作科学结果。

## 安全和限制

- `.env`、`data/`、`outputs/`、`dist/`、`drafts/`、`backups/` 和运行时目录均不提交 GitHub。
- PSL 回退使用官方已发布月平均值，不能表述为本机对逐日文件重新聚合；输出仍是单个 0.25° 格点，不是南海区域平均，也不进行趋势显著性、海洋热浪或因果归因判断。
- `sst_prepare_data` 的 PSL 月产品路径支持 `offline=true`：有经过校验的原始响应时只复用本地缓存，没有缓存时返回 `offline_unavailable`，不发起网络请求。
- 其他电脑需重新配置本机 API key，并按 README 选择 ERDDAP 日值下载或 PSL 月平均回退。
