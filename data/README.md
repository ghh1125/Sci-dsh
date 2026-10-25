# 数据将在用户主动运行后下载

本仓库不随附不完整的下载缓存、虚构海温数据或预制分析结果。

启动 `npm run web`，在 DeepSeek Harness 对话中要求“准备海温数据”；由 `sst_prepare_data` 工具从 NOAA 获取两个格点的真实日值，并生成：

- `point_A_monthly.csv`、`point_A.metadata.json`
- `point_B_monthly.csv`、`point_B.metadata.json`
- `point_B_gap_demo.csv`、`point_B_gap_demo.metadata.json`（明确标记的缺月教学副本）
- `raw/` 下的原始响应和来源记录，以及数据完整性报告
- `download_status.json` 与失败时的 `download_failures.json`：进度、错误类别和下一步建议，不保存代理凭据

每份完整月数据覆盖 1982-01 至 2025-12，共 528 个月。点 A 暂定 18.125°N、114.125°E，点 B 暂定 15.125°N、114.125°E。它们是 OISST 格点，不是在位观测站，也不是整个南海的区域平均。下载脚本负责验证响应中的坐标、日期、单位、有效值和完整日覆盖；只有验证通过后，才能继续科学分析。

字段：`month,lat,lon,sst_mean_c,n_valid_days,n_expected_days`。配套 JSON 保存产品、来源、坐标、单位、时间范围、提取记录和 SHA-256。

来源：[NOAA OISST 产品说明](https://www.ncei.noaa.gov/products/optimum-interpolation-sst)、[NOAA ERDDAP 子集入口](https://coastwatch.pfeg.noaa.gov/erddap/griddap/ncdcOisst21Agg.html)。

历史镜像可能缺少文件。脚本仅在官方明确相同的历史区间，核对 v2 与 v2.1 重叠值后使用 v2 日值补充镜像缺项，并记录具体来源；这不是插值。任何未解决的缺项都会阻止后续“完整数据”分析。详细规则见下载脚本和运行后生成的来源记录。

首次下载会先处理一个待获取年份，成功后才处理其余年份；证书/网络故障不会通过改用同服务器的旧版数据来掩盖。超时等暂时性错误会有限重试，已完成缓存会保留。失败报告区分网络错误、无效响应与科学数据不完整。

可以在本地 `.env` 配置 `SCI_DSH_DATA_PROXY`（实际 HTTP/HTTPS 代理地址）或机构 `SCI_DSH_DATA_CA_BUNDLE`（PEM 证书文件）。它们仅传给数据下载子进程，模型不需要知道其值；模型 API 网关地址不是这个数据代理。修改后需重启 Harness。

把另一台已成功准备数据的电脑的完整 `data/` 复制过来后，在 Harness 请求 `sst_prepare_data(offline=true)`，可不联网复核月数据，或从完整年度原始缓存重建。没有有效本地数据时离线准备会明确失败。离线数据模式不会替代在线模型 API。
