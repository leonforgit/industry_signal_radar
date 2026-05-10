# 配置说明

这个目录保存 `industry_signal_radar/` 的正式运行配置。

当前第一份正式配置是：

- `runtime_defaults.json`
- `source_manifest.json`
- `event_db_schema.sql`
- `radar_opportunity_snapshot_schema_v1.json`
- `radar_catalyst_inventory_schema_v1.json`
- `radar_candidate_pool_schema_v1.json`
- `radar_research_handoff_schema_v1.json`
- `radar_report_rules_v1.json`
- `industry_signal_radar.env.example`
- `systemd/industry-signal-radar.service`
- `systemd/industry-signal-radar.timer`

它的职责是把下面这些口径写死：

- 部署节点
- 共享只读市场数据入口
- 新系统独立 runtime root
- 日志、缓存、输出、状态库路径
- 轮询节奏
- Bark 的基本告警配置
- Bark 的环境变量口径与远端 env file 模板
- v1 计划接入的共享源、公告源、政策源、新闻源和数据源注册表
- 事件库的正式 schema
- Radar 排序 snapshot 的正式 JSON schema
- Radar catalyst inventory 的正式 JSON schema
- candidate pool 输入的正式 JSON schema
- research handoff 输出的正式 JSON schema
- PM 级日报模板与新鲜度门禁规则
- News Event Hub 多 consumer exports 的读取路径
- systemd service / timer 的 repo 内 source of truth

当前原则：

- 现有量化线的市场数据默认 `只读复用`
- 新系统的状态、缓存、日志、输出必须 `独立隔离`
- 后续脚本优先读取这里的配置，而不是把默认值散落到脚本里
- `source_manifest.json` 现在作为 v1 的 `source registry`
- 后续接入状态、优先级、可信度、调度类和来源分层都应优先回写到 manifest
