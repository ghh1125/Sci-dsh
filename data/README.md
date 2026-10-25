# 数据将在用户主动运行后下载

本仓库不随附不完整的下载缓存、虚构海温数据或预制分析结果。

启动 `npm run web`，在 DeepSeek Harness 对话中要求“准备海温数据”；由 `sst_prepare_data` 工具从 NOAA 获取两个格点的真实日值，并生成：

- `point_A_monthly.csv`、`point_A.metadata.json`
- `point_B_monthly.csv`、`point_B.metadata.json`
- `point_B_gap_demo.csv`、`point_B_gap_demo.metadata.json`（明确标记的缺月教学副本）
- `raw/` 下的原始响应和来源记录，以及数据完整性报告

每份完整月数据覆盖 1982-01 至 2025-12，共 528 个月。点 A 暂定 18.125°N、114.125°E，点 B 暂定 15.125°N、114.125°E。它们是 OISST 格点，不是在位观测站，也不是整个南海的区域平均。下载脚本负责验证响应中的坐标、日期、单位、有效值和完整日覆盖；只有验证通过后，才能继续科学分析。

字段：`month,lat,lon,sst_mean_c,n_valid_days,n_expected_days`。配套 JSON 保存产品、来源、坐标、单位、时间范围、提取记录和 SHA-256。

来源：[NOAA OISST 产品说明](https://www.ncei.noaa.gov/products/optimum-interpolation-sst)、[NOAA ERDDAP 子集入口](https://coastwatch.pfeg.noaa.gov/erddap/griddap/ncdcOisst21Agg.html)。

历史镜像可能缺少文件。脚本仅在官方明确相同的历史区间，核对 v2 与 v2.1 重叠值后使用 v2 日值补充镜像缺项，并记录具体来源；这不是插值。任何未解决的缺项都会阻止后续“完整数据”分析。详细规则见下载脚本和运行后生成的来源记录。
