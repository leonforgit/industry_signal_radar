# 行业起势预警项目

这个子工作区当前承接的是一个已经开始提升为通用 `Radar` 的实现位。

它的物理目录名仍然是 `industry_signal_radar/`，但当前产品定位已经不再只是狭义“行业新闻雷达”，而是一个：

- 从 `News Event Hub` 拉取共享输入的下游发现器
- 叠加自身 sidecar 信号做二次排序的 `Radar`
- 同时产出 `Bark 告警 + 日度机会排序 + 投资机会报告` 的机会发现系统

它的目标不是直接做自动交易，而是长期建设一套：

- 能捕捉行业起势预兆的研究框架
- 能承接多源信号的项目骨架
- 能部署到服务器并自动推送告警的长期系统
- 能在仓库外自管运行环境上按日自动生成并邮件投递 `Radar 投资机会日报` 的前置系统

当前项目默认围绕这些问题展开：

- 早期资金是否开始向某个行业汇集
- 行业经营状态是否出现边际改善
- 行业叙事和新闻关注是否开始扩散
- 这些变化能否被压缩成可跟踪的行业状态

<!-- codex-workspace-bootstrap:readme:start -->
## Codex Local Conventions

- Repository-level guidance lives in `AGENTS.md`; directory-specific rules live in `scripts/AGENTS.md`, `reports/AGENTS.md`, `docs/AGENTS.md`, `data/AGENTS.md`, `output/AGENTS.md`
- Prefer editing source-of-truth inputs, configs, notes, or trackers over generated outputs.
- Generated outputs typically live under `reports/`, `docs/`, `output/` and should be rebuilt through scripts when possible.
- Clickable file links should prefer the ASCII alias root `.`.

## Validation Suggestions

- `python3 scripts/build_radar_candidate_pool.py`
- `python3 scripts/build_radar_workspace_outputs.py`
- `python3 scripts/build_radar_source_readiness.py`
- `python3 scripts/build_radar_ipo_watchlist.py`
- `python3 scripts/build_radar_kimi_editorial.py`
- `python3 scripts/build_radar_kimi_research_harness.py`
- `python3 scripts/validate_radar_kimi_research.py`
- `python3 scripts/ensure_radar_freshness.py`
- `python3 scripts/check_radar_report_quality.py`
- `python3 scripts/send_radar_daily_report_email.py --report-transport local --dry-run`
- `python3 scripts/smoke_test_radar_candidate_pool.py`
- `python3 scripts/smoke_test_snapshot_bark_dispatch.py`
- `bash scripts/smoke_test_radar_workspace_outputs.sh`
<!-- codex-workspace-bootstrap:readme:end -->

## 当前目录结构

- `STATUS.md`
  - 当前阶段状态与下一步重点
- `reports/`
  - 项目的 canonical main report
- `workpapers/`
  - 外部扫描、backlog、实验过程与支持笔记
- `docs/`
  - 项目计划、架构、节点地图和工作流文档
- `config/`
  - 正式运行配置、共享 / 隔离边界定义、source manifest、event db schema 与 systemd 单元
- `scripts/`
  - 稳定脚本入口、全行业扫描 runner、Bark 推送链、runtime bootstrap、远端 wrapper 与部署脚本
- `data/`
  - 全行业 registry、小型映射表、schema 和 fixture
  - 不存放运行期大数据、缓存、状态库或抓取副本
- `output/`
  - 未来的导出摘要和预览产物

## 当前核心文档

- 开源许可证：`LICENSE`
- 安全边界与漏洞报告：`SECURITY.md`
- 贡献规则：`CONTRIBUTING.md`
- 公开发布检查清单：`docs/public_release_checklist.md`
- 系统规划总纲：`docs/radar_system_plan_v1.md`
- 统一产品说明：`docs/radar_unified_plan.md`
- Ranking 方法：`docs/radar_ranking_v1.md`
- Candidate Pool Contract：`docs/radar_candidate_pool_contract_v1.md`
- 输出 Contract：`docs/radar_output_contract_v1.md`
- 日报模板：`docs/radar_daily_report_template.md`
- PM 级日报规则：`docs/radar_pm_report_rules_v1.md`
- Radar 参考标准：`docs/radar_reference_standards_v1.md`
- Catalyst Inventory Contract：`docs/radar_catalyst_inventory_contract_v1.md`
- 执行主控：`docs/execution_checklist.md`
- 核心规划（早期背景）：`docs/master_plan.md`
- 成功标准：`docs/success_criteria.md`
- 可行性与资源规划：`docs/feasibility_and_resource_plan.md`
- 新闻源准入规则：`docs/news_source_admission_policy.md`
- 自动告警目标态：`docs/automatic_alerting_target.md`
- Radar Contract：`docs/radar_contract_v1.md`
- 行业对象层：`docs/industry_object_layer_plan.md`
- 主报告：`reports/main.md`
- 项目计划：`docs/project_plan.md`
- 系统架构：`docs/system_architecture.md`
- 节点地图：`docs/node_map.md`
- 研究 backlog：`workpapers/research_backlog.md`
- 外部扫描：`workpapers/external_scan.md`
- 新闻源研究：`workpapers/news_source_research_2026-04-03.md`
- 新闻源重叠快照：`workpapers/news_source_overlap/news_source_overlap_latest.md`
- 新闻源行业映射快照：`workpapers/news_source_mapping/news_source_mapping_latest.md`
- 新闻 overlay 分流说明：`workpapers/news_overlay_routing/news_overlay_routing_20260403.md`
- 新闻关键词候选池：`workpapers/news_keyword_candidates/news_keyword_candidates_latest.md`
- 新闻关键词审阅备忘录：`workpapers/news_keyword_candidates/news_keyword_candidate_review_20260403_round1.md`

## 当前边界

- 当前仓库按公开发布边界维护；`docs/public_release_checklist.md` 是切换可见性和后续发布前的安全检查入口。
- 先做 `研究版` 和 `预警版`，不直接做自动交易
- 覆盖目标面向 `全行业`
- 原型样板集只用于前期方法开发和校准，不代表项目边界
- 既有行业研究最多只借一点基本面思路，不作为对象模板或策略蓝本
- 不把大量原始新闻语料和运行时缓存直接堆进仓库
- 不在仓库记录 API key、token、SSH key、SSH config、known hosts、私有 hostname、私有 IP、私有端口、跳板机细节、个人路径或部署覆盖配置。
- 默认把运行期数据、缓存、状态库、健康快照和导出结果放在仓库外自管运行环境，本地只保留小型 source-of-truth 映射表
- 当前 `Radar 投资机会日报` 的生成和发送已经是脚本原生链路，不依赖 Codex 或 agents 常驻参与
- 当前 Radar 已开始消费量化主系统产出的情绪 sidecar：
  - 市场级：`资金流情绪 / 市场事件情绪 / 综合情绪`
  - 公司级：`事件情绪 / 行情情绪 / 综合情绪`
- 当前情绪分数只作为排序辅助和 PM 解释层，不替代 `catalyst / evidence / milestone`
- 远端连接信息：不在仓库内记录；通过私有环境变量注入。
- 旧私有跳板入口只作为仓库外运维兜底，不在公开仓库记录连接细节。
- 备用宿主入口：不在仓库内记录。
- 当前已经具备可运行的 `MVP scanner / runner`
- 当前 `news_cctv` 已作为第一批新闻 / 政策层接入
- 当前新闻 / 政策层已经开始优先读取 `News Event Hub` 导出的共享 feed：
  - `shared_news_event_hub.industry_radar_feed_path`
  - 默认路径：`/opt/news-event-hub/state/consumer_exports/industry_radar_feed_latest.json`
  - 当前日报主链默认不再回退到私有新闻 overlay
- 当前工作区虽然仍以 `Industry Radar` 为实现入口，但新的 contract 已开始按更通用的 `Radar` 来定义：共享新闻事件由 `News Event Hub` 提供，Radar 作为下游发现器来 pull、排序并产出日度机会报告
- 当前日报主链已经切到 `canonical-first`：
  - 事件层只消费 `News Event Hub` consumer feeds
  - 情绪层只消费量化主系统 sentiment sidecar
  - 市场层只消费 canonical market substrate 与 equity price substrate
- 当前 `hog_cycle_proxy` 已作为第一批行业专属基本面代理样板接入
- 当前 `shipping_cycle_proxy` 已作为第二个行业专属基本面代理样板接入
- 当前已经补出第一版 `representative stock / ETF proxy`
- 当前资金层已经补出 `成交占比 / 内部上涨占比 / 龙头牵引 + 行业资金流 / ETF / 北向 / 两融 / 龙虎榜`
- 当前公告层已经接入 `stock_notice_report` 的第一版行业压缩

## 当前工作方法

- 默认先看 `docs/execution_checklist.md`
- 如需理解系统总规划，优先看 `docs/radar_system_plan_v1.md`
- 再看 `docs/radar_unified_plan.md` 理解当前产品定位、Ranking、Bark 和日报语义
- `docs/master_plan.md` 只作为早期背景规划补充参考
- 每完成一轮推进后，先回写执行主控文档，再更新其他文档
- 需要生成 `PM` 可用正式作战单时，优先跑 `python3 scripts/build_radar_workspace_outputs.py`；通过质量门禁后消费 `output/reports/radar_daily_battlecard_latest.md/.pdf`。`radar_daily_report_latest.*` 是完整日报渲染的中间 latest，`radar_intraday_scan_report_latest.md` 才是盘中扫描预览，二者不要混用。完整 build 还会写出 `output/runs/radar_harness_manifest_latest.json`，用于追踪每个节点的输入、输出、耗时、降级和失败点。
- Harness 的冻结合同和缺陷账本见 `docs/radar_harness_contract_v1.md`；后续 review 先把 finding 归入 active/fixed/obsolete/deferred，再决定是否改代码。
- 需要检查 remote runtime 每日邮件投递链时，优先看 `industry-signal-radar-daily-report.timer`、`health/radar_daily_email_delivery_latest.json` 和 `scripts/send_radar_daily_report_email.py`；timer 单元按每天 `08:15 CST` 开盘前晨报配置，但公开安装脚本默认只安装不启用日报邮件 timer，必须在 SMTP dry-run 验证后显式传 `--enable-daily-report-timer`。
- 当前日报邮件默认以 `PDF` 为正式附件，邮件正文只保留摘要
- 当前日报链在生成前会先跑 `source readiness`：只有 `News Event Hub / sentiment sidecar / canonical market substrate` 三层都通过 freshness 门禁，日报和邮件才应继续往下走；同时会读取 `News Event Hub` 的 `source_health_latest.json`，把 degraded / down 源带入 quality warning，不再只凭 feed 时间戳放行。
- 当前 `data substrate audit` 负责暴露价格、财务、辅助源的持续缺口；这些缺口默认进入 `warn`，不再直接阻塞日报主链
- 当前日报链已经补上两条主动上游调用能力：
  - `ensure_radar_price_freshness.py` 会先对当前候选个股做逐票 freshness 检查；如果价格 sidecar 缺口仍在，会优先调用 `build_radar_canonical_price_bridge.py` 逐票抓取最新行情、写回 canonical market DB，并把结果并入 `company_price_snapshot_latest.csv`
  - 如果逐票补抓仍不足，`ensure_radar_price_freshness.py` 会优先快速返回 `warn` 并延后重型补数，避免少量边缘公司价格缺口拖死日报主链；需要手动补齐时再显式使用 `--force-refresh`
  - `build_radar_news_verification_sidecar.py` 会先用 `research_feed_latest.json` 做快速核实，只在证据偏薄的前排公司对象上按需触发 `News Event Hub` 的 `run_company_discovery.py`
- 当前日报正文也会显式披露这两层结果：顶部摘要会说明 `价格补数 / 新闻补核` 是否运行成功，前排对象会在有结果时带出 `新闻补核` 摘要
- 当前邮件发送默认是非阻塞增强层：`RADAR_SMTP_REQUIRED=0` 时 SMTP 失败只留下 warn；需要把发信失败视作硬失败时再设 `RADAR_SMTP_REQUIRED=1`
- 当前日报默认切成 `morning_brief`：主报告控制在晨会前可读的一到两页，完整分桶、源标签、方法说明和长附录默认留在 handoff / snapshot / quality sidecar。
- 当前日报链也已经支持一层可选的 `Kimi editorial layer`：它消费 `snapshot / handoff / quality / source_readiness / 公司补核 / IPO watchlist` 等结构化产物，成功时把 PM 摘要、初步研究、IPO 打新初筛和风险提示并进正式 PDF；失败时自动降级，不阻塞日报主链。
- 当前日报链已经新增强制 `Kimi research harness`：它在渲染前对 Top/递补候选做结构化预检，输出 `promote / keep / watch_only / reject / risk_review / research_gap` verdict，并由 quality gate 确认 Top Opportunities 全部经过 Kimi 预检且没有降权/拒绝对象。
- 当前正式邮件链只读取 `radar_daily_battlecard_*` 稳定别名；这些别名只能由完整 workspace build 在 `quality = pass` 且 `snapshot / report / quality run_id` 一致后发布，盘中扫描不会覆盖正式作战单入口。
- 当前完整 workspace build 与盘中扫描共享 `state/radar_workspace_outputs.lock`：日报构建持锁时，高频扫描会返回 `workspace_output_lock_held` 并跳过，避免中途覆盖 `snapshot / handoff / inventory latest`。
- 当前 Agent Harness 已落地可执行任务队列：`scripts/build_radar_agent_task_queue.py` 会把 Kimi 降级、新闻补核缺口、共享新闻源降级、邮件投递失败等转成 `output/agent_tasks/radar_agent_task_queue_latest.json/.md`，每条任务都带 owner agent、允许修改范围、上下文路径、repair 命令和验收命令；claim/start/complete 受锁、lease 和 result artifact 约束。
- 当前 A/H IPO 打新入口由 `build_radar_ipo_watchlist.py` 与 `build_radar_hk_ipo_watchlist.py` 生成：日报会单列可申购/待申购与不达标/跳过对象；港股侧会尽量抓取 HKEX 招股书、申购窗口、发售价、可比估值、保荐人、基石和近期认购热度，核心结论是是否值得申购，不写成上市后二级买入。

当前第一轮细化已经从 `行业对象层` 开始，并配有原型样板清单与覆盖层说明：

- `data/industry_archetype_seed_objects.csv`
- `data/industry_universe_layers.csv`

当前新增的全行业运行骨架包括：

- `data/industry_registry_sw_level1.csv`
- `data/industry_etf_proxy_candidates.csv`
- `data/industry_etf_proxies_primary.csv`
- `data/industry_representative_stock_candidates.csv`
- `data/industry_representative_stocks_primary.csv`
- `data/theme_chain_overlays.csv`
- `data/theme_chain_overlay_members.csv`
- `data/non_industry_news_overlays.csv`
- `scripts/radar_industry_registry.py`
- `scripts/build_etf_proxy_candidates.py`
- `scripts/build_representative_stock_candidates.py`
- `scripts/radar_flow_signals.py`
- `scripts/radar_announcements.py`
- `scripts/radar_scan_runner.py`
- `scripts/radar_bark.py`
- `scripts/radar_company_mapping.py`
- `scripts/radar_news_overlay_routing.py`
- `scripts/radar_news_policy.py`
- `scripts/radar_shared_news.py`
- `scripts/radar_fundamental_proxy.py`
- `scripts/radar_validation_summary.py`
- `scripts/evaluate_news_industry_mapping.py`
- `scripts/evaluate_news_source_overlap.py`
- `scripts/build_news_keyword_candidate_report.py`
- `scripts/build_company_mapping_cache.py`
- `scripts/smoke_test_shared_news_overlay.py`
- `scripts/build_radar_opportunity_snapshot.py`
- `scripts/build_radar_candidate_pool.py`
- `scripts/build_radar_bark_summary.py`
- `scripts/build_radar_catalyst_inventory.py`
- `scripts/render_radar_daily_report.py`
- `scripts/build_radar_workspace_outputs.py`
- `scripts/ensure_radar_price_freshness.py`
- `scripts/build_radar_news_verification_sidecar.py`
- `scripts/build_radar_ipo_watchlist.py`
- `scripts/build_radar_kimi_editorial.py`
- `scripts/ensure_radar_freshness.py`
- `scripts/radar_freshness_utils.py`
- `scripts/check_radar_report_quality.py`
- `scripts/send_radar_daily_report_email.py`
- `scripts/run_radar_daily_report_email.sh`
- `scripts/radar_sentiment_sidecar.py`
- `scripts/install_radar_daily_smtp_env.sh`
- `scripts/validate_radar_catalyst_inventory.py`
- `scripts/check_radar_contract_cases.py`
- `scripts/smoke_test_radar_candidate_pool.py`
- `scripts/smoke_test_snapshot_bark_dispatch.py`
- `scripts/smoke_test_radar_workspace_outputs.sh`
- `scripts/validate_remote_industry_signal_radar.sh`
