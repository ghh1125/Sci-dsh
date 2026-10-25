# Harness 实际发现的 Skill 目录

初始为空，以便现场展示“先完成分析，再生成和导入 Skill”。

进入 DeepSeek Harness 后：先分析 A，调用 `sst_create_skill` 从成功运行生成草稿，再调用 `sst_export_skill` 和 `sst_install_skill` 导出、导入。安装成功后调用 `skill(name="ocean-sst-analysis")`，新任务即可复用。

预置的科学方法模板和基础计算工具在 `skill-templates/ocean-sst-analysis/`。它不是已经从本次运行学习到的成果；创建工具会将模板与真实成功运行证据结合生成草稿。完整项目的安装、配置和演示步骤见根目录 README。
