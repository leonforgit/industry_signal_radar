# Radar Contract V1

## 目的

这份文档定义当前 `industry_signal_radar` 子工作区对外承担的 `Radar` contract。

如果需要看更完整的 `Radar 本体 / Ranking / Bark / 日报` 统一口径，优先参考：

- `docs/radar_unified_plan.md`

虽然当前实现工作区仍叫 `Industry Signal Radar`，但 contract 已经开始向更通用的 `Radar` 方向收拢。

它回答的问题是：

- Radar 从哪里读共享机会输入
- Radar 自己负责什么
- Radar 输出什么

## 一句话结论

`Radar` 不是新闻真相源，而是共享事件层的下游发现器。

它默认：

- 从 `News Event Hub` pull 数据
- 叠加价格、资金、proxy、公告、已有覆盖度
- 产出每日机会面板、快照和告警

## 输入边界

### 主输入：来自 News Event Hub

Radar 默认从共享 consumer exports 读取：

- `industry_radar_feed_latest.json`
- `opportunity_report_feed_latest.json`
- `research_feed_latest.json`
- `source_health_latest.json`

这些输入提供：

- shared events
- event_state
- score_vector
- entity mapping
- opportunity candidate

### 辅输入：Radar 自己的 sidecar

Radar 还会叠加：

- 价格与技术面
- 资金面
- 公告压缩
- proxy variables
- 代表股 / ETF / 主题 overlay
- 当前研究覆盖与 watchlist 上下文

## Radar 负责什么

### 1. 机会发现

回答：

- 今天哪些对象值得先看
- 为什么值得先看
- 当前更像 `噪音 / 观察 / 共振 / 强提醒`

### 2. 跨层共振排序

Radar 把共享事件输入和自己的 sidecar 合并，用于形成：

- 日度机会排序
- 快照
- 告警

### 3. 对象分桶

V1 建议 Radar 至少支持这些对象类型：

- `macro`
- `industry`
- `company`
- `special_situation`
- `watchlist_priority_change`

当前仓库仍以 `industry` 为主实现，但 contract 不应再把它限制死为“只能看行业”。

## Radar 不负责什么

Radar 不应负责：

- 新闻抓取
- article normalize
- article -> event merge
- canonical entity mapping
- 把自己变成 Qlib 的唯一事件真相源

这些职责留在 `News Event Hub`。

## 输出 contract

Radar 对外最小输出建议包含：

- `radar_object_type`
- `radar_object_id`
- `radar_object_name`
- `radar_score`
- `radar_bucket`
- `confidence`
- `supporting_events`
- `key_evidence`
- `followup_path`
- `alert_level`
- `generated_at`

## 每日机会报告语义

Radar 的日报输出应该回答：

1. 今天先看什么
2. 为什么是这些对象
3. 它们各自属于哪种机会桶
4. 哪些只是观察，哪些值得立刻研究

## 与 Qlib 的关系

Radar 不应成为 Qlib 的主事件上游。

更合理的结构是：

- `News Event Hub -> Qlib`
- `News Event Hub -> Radar`

如果后续需要，Radar 只额外输出自己的二级衍生特征给 Qlib，例如：

- `radar_score`
- `alert_level`
- `multi_resonance_score`
- `radar_bucket`

## 当前结论

当前 `industry_signal_radar` 仍是实现位，但 contract 已经开始往更通用的 `Radar` 收。

后续如果继续推进：

- 名字可以保持不变一段时间
- 但 contract 应按照“全工作区机会发现器”来设计，而不是继续只按行业专用逻辑收口
