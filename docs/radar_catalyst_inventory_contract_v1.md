# Radar Catalyst Inventory Contract V1

这份文档定义 `Radar catalyst inventory` 的角色和最小 contract。

它不是又一份日报，也不是 snapshot 的重复副本，而是 `event-driven 前置系统` 的持续事件账本。

## 1. 目标

`catalyst inventory` 负责把单轮 snapshot 变成跨天可跟踪的对象状态：

- 一个对象第一次出现时，知道它是 `首次入库`
- 同一个对象连续几轮还在时，知道它是 `持续跟踪`
- 研究优先级变化时，知道它是 `状态迁移`
- 对象连续缺席时，知道它已经 `expired`

它的作用是把 Radar 从“每天重新排一遍榜”推进到“维护一份持续 catalyst book”。

## 2. 输出位置

- JSON：`output/inventory/radar_catalyst_inventory_latest.json`
- Markdown：`output/inventory/radar_catalyst_inventory_latest.md`
- Schema：`config/radar_catalyst_inventory_schema_v1.json`
- Validator：`scripts/validate_radar_catalyst_inventory.py`
- Builder：`scripts/build_radar_catalyst_inventory.py`

主编排链里，inventory 固定在：

`snapshot -> validate snapshot -> catalyst inventory -> validate inventory -> Bark / handoff / daily report`

## 3. 状态机语义

当前 v1 使用这 6 个 inventory state：

- `open`
  - 对象已经进入 Radar，但还没有升到持续研究层
- `watching`
  - 对象已经进入 `thesis_watch` 或持续候选层，值得继续盯里程碑
- `confirmed`
  - 对象已经进入 `immediate_research`
- `risk_review`
  - 对象当前更像风险复核，而不是正向机会推进
- `background`
  - 对象只保留背景观察角色
- `expired`
  - 对象已经连续缺席多轮 snapshot，不再保留为 active catalyst

同时保留两层存在性语义：

- `active`
- `missing_recent`

也就是说：

- `state` 回答对象当前属于哪类 catalyst lifecycle
- `presence_status` 回答对象这轮是否真的还在 live snapshot 里

## 4. 最小字段

### 4.1 身份与时间

- `inventory_id`
- `radar_object_id`
- `radar_object_name`
- `radar_object_type`
- `first_seen_at`
- `first_seen_sample_date`
- `last_seen_at`
- `last_seen_sample_date`
- `last_seen_run_id`

### 4.2 当前状态

- `current_state`
- `previous_state`
- `presence_status`
- `state_changed`
- `seen_count`
- `missed_runs`
- `state_transition_count`
- `transition_note`

### 4.3 当前事件语义

- `radar_bucket`
- `triage_action`
- `event_driven_lane`
- `catalyst_type`
- `hard_or_soft`
- `catalyst_stage`
- `evidence_quality`
- `alert_level`

### 4.4 研究承接信息

- `radar_score`
- `rank_overall`
- `next_milestone`
- `milestone_due_window`
- `confirmation_gap`
- `failure_mode`
- `primary_symbols`
- `etf_proxies`
- `theme_overlays`
- `research_links`

### 4.5 历史轨迹

- `state_history[]`
  - `run_id`
  - `as_of_date`
  - `inventory_state`
  - `presence_status`
  - `radar_bucket`
  - `triage_action`
  - `radar_score`

## 5. v1 规则

当前 v1 是 `heuristic inventory`，先解决“持续跟踪”和“状态迁移”问题，不假装已经解决全部研究闭环。

默认规则：

- `immediate_research` -> `confirmed`
- `thesis_watch` 或持续候选 -> `watching`
- `risk_review` / `risk_monitor` -> `risk_review`
- 早期但未进入主研究层 -> `open`
- 纯背景对象 -> `background`
- 连续缺席达到阈值 -> `expired`

这版 state machine 的重点是：

- 先让对象跨天可追踪
- 先让 handoff / 日报 / 质量检查都消费同一份 book
- 后续再把它推进到真正的 `research memory / close-out / replay`

## 6. 与其他输出面的关系

### 6.1 日报

日报应直接引用 inventory，至少显示：

- 当前 inventory 总体分布
- Top Opportunities 的 `previous_state -> current_state`
- 该对象已持续出现多少轮

### 6.2 Research Handoff

handoff 不只是列出对象，也要告诉研究端：

- 这是第一次看到，还是已经跟了几轮
- 这轮有没有状态升级
- 当前对象是 `confirmed`、`watching` 还是 `risk_review`

### 6.3 质量检查

质检脚本应至少确认：

- inventory 存在
- inventory `run_id` 与 snapshot 对齐
- 旧样本时要明确给出 stale warning，而不是默认“日报没问题”

## 7. 当前边界

这份 v1 contract 还没有解决：

- 研究是否真的被承接
- 承接后结论是什么
- 对象何时被人工关闭
- 对象是 `confirmed` 还是只是 `暂时高优先级`

这些属于下一阶段的 `research memory / replay` 闭环，而不是当前这份 inventory contract 要一次性吃掉的范围。
