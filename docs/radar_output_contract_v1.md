# Radar Output Contract V1

## 0. 文档定位

这份文档把 `Ranking v1` 进一步落成 `Radar 输出 contract`。

如果要看当前工作区最高优先级的系统总规划，优先参考：

- `docs/radar_system_plan_v1.md`

它回答 5 个问题：

- Radar 排序结果最小要输出哪些字段
- JSON 快照应该长什么样
- research handoff 应该消费哪些字段
- 日报表格应该消费哪些字段
- Bark 触发应该依赖哪些字段

当前目标不是一次性设计最终 schema，而是先固定一版：

- 能让排序结果稳定落地
- 能直接驱动日报
- 能直接驱动 Bark
- 能直接驱动研究交接板
- 能支持后续验证与复盘

当前 repo 内的首版正式 schema 与 fixture 约定为：

- `config/radar_opportunity_snapshot_schema_v1.json`
- `config/radar_research_handoff_schema_v1.json`
- `data/radar_opportunity_snapshot_fixture_v1.json`

如果要看 snapshot 上游那一层的输入 contract，参考：

- `docs/radar_candidate_pool_contract_v1.md`

## 1. 设计原则

### 原则 1

同一个排序对象，应同时服务：

- `snapshot JSON`
- `Markdown 日报`
- `Bark 触发`
- `research handoff`
- `验证与复盘`

### 原则 2

contract 必须显式表达：

- 对象是谁
- 为什么现在值得看
- 当前属于哪个 bucket
- 是否该触发 Bark
- 下一步研究动作是什么

### 原则 3

在 `早发现优先` 的前提下，contract 必须保留“线索但未确认”的表达能力，不能只接受高确定性对象。

## 2. 顶层输出

`Radar v1` 建议默认产出四类核心文件：

1. `ranking snapshot`
2. `bark dispatch summary`
3. `research handoff`
4. `daily report`

建议路径：

- `output/snapshots/radar_opportunity_snapshot_YYYYMMDD.json`
- `output/snapshots/radar_opportunity_snapshot_latest.json`
- `output/snapshots/radar_bark_summary_YYYYMMDD.json`
- `output/snapshots/radar_bark_summary_latest.json`
- `output/handoffs/radar_research_handoff_YYYYMMDD.md`
- `output/handoffs/radar_research_handoff_latest.md`
- `output/handoffs/radar_research_handoff_YYYYMMDD.json`
- `output/handoffs/radar_research_handoff_latest.json`
- `output/reports/radar_daily_report_YYYYMMDD.md`
- `output/reports/radar_daily_report_latest.md`
- `output/reports/radar_daily_report_YYYYMMDD.html`
- `output/reports/radar_daily_report_latest.html`
- `output/reports/radar_daily_report_YYYYMMDD.pdf`
- `output/reports/radar_daily_report_latest.pdf`
- `output/reports/radar_daily_battlecard_latest.md`
- `output/reports/radar_daily_battlecard_latest.html`
- `output/reports/radar_daily_battlecard_latest.pdf`
- `output/reports/radar_daily_battlecard_quality_latest.json`
- `output/snapshots/radar_daily_battlecard_snapshot_latest.json`
- `output/reports/radar_intraday_scan_report_latest.md`

`radar_daily_battlecard_*` 是正式发布入口，只能由完整 workspace build 在质量门禁通过后发布；`radar_intraday_scan_report_latest.md` 是盘中扫描预览，不能作为每日邮件或 PM 正式作战单附件。

完整 workspace build 与盘中扫描共享 `state/radar_workspace_outputs.lock`。日报构建持锁时，扫描任务应直接跳过本轮写入，避免 `snapshot / handoff / inventory latest` 在正式日报链中途被覆盖。

## 3. Ranking Snapshot 顶层结构

建议 `ranking snapshot` 顶层结构如下：

```json
{
  "generated_at": "2026-04-11T09:30:00Z",
  "radar_run_id": "scan:2026-04-11T09:20:00Z",
  "as_of_date": "2026-04-11",
  "market_tz": "Asia/Shanghai",
  "ranking_version": "v1",
  "preference_profile": {
    "early_discovery_first": true,
    "mixed_object_board": true,
    "bucketed_report": true
  },
  "market_sentiment_context": {
    "as_of_date": "2026-04-11",
    "lag_days": 0,
    "market_flow_sentiment": 0.12,
    "market_flow_score": 56,
    "market_flow_label": "中性",
    "market_event_sentiment": -0.08,
    "market_event_score": 46,
    "market_event_label": "中性",
    "market_composite_sentiment": 0.04,
    "market_composite_score": 52,
    "market_composite_label": "中性"
  },
  "summary": {
    "candidate_count": 0,
    "strong_alert_count": 0,
    "strong_candidate_count": 0,
    "research_candidate_count": 0,
    "observe_count": 0,
    "bark_trigger_count": 0
  },
  "objects": []
}
```

## 4. 单对象最小 contract

每个排序对象建议至少包含以下字段。

### 4.1 对象身份

- `radar_object_type`
- `radar_object_id`
- `radar_object_name`
- `radar_object_scope`

说明：

- `radar_object_type`
  - `industry / macro / company / special_situation / watchlist_priority_change`
- `radar_object_scope`
  - 用于表达地域、市场、主题域或覆盖范围

### 4.2 排序结果

- `runtime_state`
- `radar_bucket`
- `radar_score`
- `rank_in_bucket`
- `rank_overall`
- `alert_level`

说明：

- `runtime_state`
  - `cold / warming / candidate / strong_alert`
  - 它属于运行状态机语义
- `radar_bucket`
  - `observe / research_candidate / strong_candidate / strong_alert`
  - 它属于排序 / 日报语义
- `alert_level`
  - `none / watch / candidate / strong_alert`

### 4.2.2 情绪 sidecar 字段

- 顶层：
  - `market_sentiment_context`
- 公司对象可选：
  - `sentiment_context`

说明：

- `market_sentiment_context`
  - 承接市场级 3 分：
    - `market_flow_sentiment`
    - `market_event_sentiment`
    - `market_composite_sentiment`
  - 它主要服务：
    - 日报顶部的市场风险偏好背景
    - `soft catalyst` 的告警门控
  - 它不应直接主导对象排序

- `sentiment_context`
  - 主要挂在 `company` 对象上，承接公司级 3 分：
    - `company_event_sentiment`
    - `company_market_sentiment`
    - `company_composite_sentiment`
  - 它主要服务：
    - 公司对象的排序辅助
    - `why_now / confidence` 的解释增强
    - PM 对单一对象“事件是否开始被市场承认”的快速判断

这里的原则要固定：

- Radar 不自己重算一套独立情绪
- Radar 消费上游结构化情绪 sidecar
- 情绪只能做 sidecar，不能替代 `catalyst / evidence / milestone`

### 4.2.1 事件驱动中枢字段

- `catalyst_type`
- `hard_or_soft`
- `catalyst_stage`
- `next_milestone`
- `milestone_due_window`
- `event_driven_lane`
- `confirmation_gap`
- `hedge_difficulty`
- `failure_mode`
- `evidence_quality`
- `triage_action`

说明：

- `catalyst_type`
  - `earnings_guidance / approval_registration / order_project / capital_markets / distress_delisting / industry_proxy / policy_regulation / macro_geopolitical / product_operation / general_corporate`
- `hard_or_soft`
  - `hard / soft`
- `catalyst_stage`
  - `announced / execution_window / confirmation_window / signal_clustered / monitoring / active_risk`
- `next_milestone`
  - 这个对象下一步最该看的里程碑
- `milestone_due_window`
  - `days_1_3 / days_3_10 / weeks_2_6 / open_ended`
- `event_driven_lane`
  - `hard_catalyst_board / soft_catalyst_watchlist / risk_monitor / background_monitor`
- `confirmation_gap`
  - 当前还缺哪一层确认，才能更放心地把它当成事件驱动机会
- `hedge_difficulty`
  - `low / medium / high`
- `failure_mode`
  - 如果这条逻辑走错了，最可能是怎么错的

这 9 个字段是当前工作区从“新闻雷达”升级成“事件驱动前置层”的第一批正式 contract 字段。

这里的 `evidence_quality / triage_action` 已经不再只是展示语义，它们开始直接影响：

- `Bark` 的触发资格
- `research handoff` 的分流
- 人工研究队列的优先顺序

这里要明确区分：

- 运行状态机：表达扫描链当前看到的运行层成熟度
- 排序 bucket：表达今天在研究优先级里应该放在哪一档

### 4.3 排序维度

- `worth_watching`
- `why_now_strength`
- `confidence`
- `followup_value`

当前 v1 默认固定为 `0-100` 整数尺度。

### 4.4 解释字段

- `why_now`
- `key_evidence`
- `supporting_events`
- `followup_path`
- `risk_flags`
- `confirmation_gap`
- `failure_mode`

说明：

- `why_now`
  - 一句话解释为什么今天看
- `key_evidence`
  - 少量核心证据点
- `supporting_events`
  - 支撑它的共享事件或 Radar sidecar 事件
- `followup_path`
  - 下一步研究动作
- `risk_flags`
  - 当前主要失真风险
- `confirmation_gap`
  - 当前还缺哪一层确认
- `failure_mode`
  - 这条逻辑最可能在哪里失效
- `evidence_quality`
  - `structured_confirmed / proxy_confirmed / early_thematic / narrative_only / risk_signal`
- `triage_action`
  - `immediate_research / thesis_watch / risk_review / background_only`

### 4.5 状态字段

- `is_new`
- `is_upgraded`
- `previous_bucket`
- `trigger_state`
- `dedup_key`

说明：

- `is_new`
  - 今天第一次进入主要候选池
- `is_upgraded`
  - 相比上一轮 bucket 提升
- `trigger_state`
  - `report_only / bark_candidate / bark_sent / suppressed`

### 4.6 研究连接字段

- `primary_symbols`
- `etf_proxies`
- `theme_overlays`
- `research_links`

这些字段的目的是让对象能自然导向下一步研究，而不是停留在抽象标签。

## 5. 单对象 JSON 示例

```json
{
  "radar_object_type": "industry",
  "radar_object_id": "sw_l1_801170",
  "radar_object_name": "交通运输",
  "radar_object_scope": "CN A-share",
  "runtime_state": "candidate",
  "radar_bucket": "strong_candidate",
  "radar_score": 82,
  "rank_in_bucket": 1,
  "rank_overall": 2,
  "alert_level": "candidate",
  "catalyst_type": "industry_proxy",
  "hard_or_soft": "soft",
  "catalyst_stage": "monitoring",
  "next_milestone": "复核航运 proxy 变化是否持续",
  "milestone_due_window": "weeks_2_6",
  "event_driven_lane": "soft_catalyst_watchlist",
  "evidence_quality": "proxy_confirmed",
  "triage_action": "immediate_research",
  "confirmation_gap": "当前仍缺公告硬信息确认。",
  "hedge_difficulty": "low",
  "failure_mode": "若代理变量和价格结构不能连续共振，当前更可能只是阶段性扰动。",
  "worth_watching": 84,
  "why_now_strength": 88,
  "confidence": 71,
  "followup_value": 86,
  "why_now": "共享事件热度抬升，同时资金和航运代理变量出现新增确认。",
  "key_evidence": [
    "共享事件出现跨源确认",
    "航运 proxy 出现新增增强",
    "相关市场代理活跃度抬升"
  ],
  "supporting_events": [
    {
      "source": "news_event_hub",
      "event_id": "evt_001",
      "event_type": "industry_event"
    }
  ],
  "followup_path": [
    "复核航运 proxy 变化是否持续",
    "检查代表股与 ETF 是否同步确认",
    "确认是否有政策或公告层跟进"
  ],
  "risk_flags": [
    "新闻确认仍偏早期",
    "可能受单日交易扰动影响"
  ],
  "is_new": true,
  "is_upgraded": false,
  "previous_bucket": "research_candidate",
  "trigger_state": "bark_candidate",
  "dedup_key": "industry:sw_l1_801170",
  "primary_symbols": ["601919.SH"],
  "etf_proxies": ["516780.SH"],
  "theme_overlays": ["shipping_chain"],
  "research_links": []
}
```

## 6. 日报消费字段

Radar 日报不必消费 snapshot 的所有字段，但建议至少消费下面这组。

### 6.1 分桶面板表格

建议字段：

- `radar_object_name`
- `radar_object_type`
- `runtime_state`
- `radar_bucket`
- `rank_in_bucket`
- `radar_score`
- `why_now`
- `alert_level`
- `followup_path`

### 6.2 重点详解

建议字段：

- `why_now`
- `key_evidence`
- `risk_flags`
- `confirmation_gap`
- `hedge_difficulty`
- `failure_mode`
- `evidence_quality`
- `triage_action`
- `followup_path`
- `primary_symbols`
- `etf_proxies`
- `theme_overlays`

### 6.3 Bark 回顾

建议字段：

- `radar_object_name`
- `alert_level`
- `is_new`
- `is_upgraded`
- `trigger_state`
- `why_now`

## 7. Bark 触发字段

`Bark` 不应该从 Markdown 回读，而应该直接消费 snapshot 中的状态字段。

建议 Bark 判断至少依赖：

- `runtime_state`
- `radar_bucket`
- `alert_level`
- `why_now_strength`
- `confidence`
- `followup_value`
- `is_new`
- `is_upgraded`
- `dedup_key`
- `trigger_state`

### 7.1 建议 Bark 触发最小语义

一个对象进入 Bark 候选，建议至少满足：

- `radar_bucket = strong_alert`
  或
- `alert_level = strong_alert`

并且同时满足：

- `is_new = true` 或 `is_upgraded = true`
- 不处于 suppress window
- `followup_value` 不低
- `triage_action = immediate_research`
- `evidence_quality` 至少达到 `structured_confirmed / proxy_confirmed`

### 7.2 建议 Bark payload 最小字段

- `title`
- `body`
- `group`
- `level`
- `isArchive`
- `dedup_key`
- `radar_object_id`

### 7.3 Bark body 生成建议

建议直接由这些字段拼接：

- `radar_object_name`
- `radar_bucket`
- `why_now`
- `key_evidence` 中最关键的 1-2 条
- `confirmation_gap`
- `followup_path` 中最关键的 1 条

## 8. Research Handoff 字段

`research handoff` 当前是把 Radar 从“排序结果”推进到“研究分流板”的关键输出。

建议至少消费：

- `radar_object_name`
- `radar_object_type`
- `radar_bucket`
- `evidence_quality`
- `triage_action`
- `triage_reason`
- `why_now`
- `key_evidence`
- `supporting_events`
- `confirmation_gap`
- `next_milestone`
- `milestone_due_window`
- `research_question`
- `research_checkpoints`
- `followup_path`
- `hedge_difficulty`
- `failure_mode`
- `primary_symbols`
- `etf_proxies`
- `theme_overlays`

当前分流口径固定为：

- `immediate_research`
  - 进入 `Immediate Research Queue`
- `thesis_watch`
  - 进入 `Thesis Watch`
- `risk_review`
  - 进入 `Risk Review`
- `background_only`
  - 不进入 handoff 主板

当前 handoff 建议同时保留：

- `Markdown`
  - 给人工阅读
- `JSON`
  - 给后续 research pipeline / automation / handoff agent 直接消费

## 9. 日报模板 contract

日报建议固定成下面 8 个区块：

1. 顶部摘要
2. 事件驱动面板
3. 研究分流板
4. 分桶面板
5. 今日 Top Opportunities
6. 新增 / 升级 / 观察池
7. Bark 事件回顾
8. 附录

每个区块都应由 snapshot 字段直接渲染，而不是依赖人工重写逻辑。

## 10. 与验证层的关系

当前 contract 还应支持后续验证，因此建议保留：

- `generated_at`
- `radar_run_id`
- `ranking_version`
- `previous_bucket`
- `is_new`
- `is_upgraded`
- `dedup_key`

这几类字段后面会直接影响：

- 排序观察期回看
- Bark 去重与升级判断
- “日报前排是否真的更值得研究”的统计

## 11. 当前实现顺序建议

当前最自然的实现顺序是：

1. 先固定 snapshot JSON schema
2. 再固定日报 Markdown 模板
3. 再把 Bark 直接接到 snapshot 字段
4. 最后才继续调 `radar_score` 和各维度权重

## 12. 当前一句话判断

`Radar Output Contract V1` 的核心，不是把字段堆满，而是保证同一份排序结果能同时驱动快照、Bark、research handoff、日报和复盘。
