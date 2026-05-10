# Radar Candidate Pool Contract V1

## 0. 文档定位

这份文档固定 `shared feed -> candidate pool` 这一层的输入 contract。

它回答 3 个问题：

- 上游共享 feed 到底提供什么
- Radar sidecar 在 candidate pool 里怎么补充
- 什么样的对象可以进入后续 ranking

当前 repo 内对应的 schema 与 fixture 为：

- `config/radar_candidate_pool_schema_v1.json`
- `data/radar_candidate_pool_fixture_v1.json`

## 1. 角色边界

### `News Event Hub`

只负责提供：

- 共享事件
- canonical mapping
- consumer export

它不负责：

- Radar 内部排序
- Bark 触发
- 日报渲染

### `Radar`

负责：

- 读取共享 feed
- 叠加 sidecar 信号
- 把对象压成 candidate pool
- 再进入 ranking / Bark / 日报

## 2. Candidate Pool 顶层结构

建议固定为：

```json
{
  "generated_at": "2026-04-12T09:05:00Z",
  "candidate_pool_run_id": "candidate-pool:2026-04-12T09:00:00Z",
  "as_of_date": "2026-04-12",
  "source_contract_version": "v1",
  "shared_feed_input": {},
  "sidecar_inputs": [],
  "candidates": []
}
```

## 3. 顶层字段解释

- `shared_feed_input`
  - 共享输入是谁、什么时候生成、由哪个 consumer 消费
- `sidecar_inputs`
  - 本轮补充了哪些 Radar 内部信号源
- `candidates`
  - 真正进入后续 ranking 的候选对象

## 4. 单候选对象最小字段

- `candidate_id`
- `radar_object_type`
- `radar_object_id`
- `radar_object_name`
- `radar_object_scope`
- `candidate_origin`
- `shared_feed_events`
- `sidecar_evidence`

其中：

- `candidate_origin`
  - 固定表达对象来自 `shared_feed / sidecar` 的哪一边
- `shared_feed_events`
  - 保留共享事件层的直接来源
- `sidecar_evidence`
  - 表达资金、公告、proxy 等内部补充证据

## 5. V1 准入原则

当前 V1 默认允许这些对象进入 candidate pool：

- `industry`
- `macro`
- `company`
- `special_situation`
- `watchlist_priority_change`

当前不要求所有对象都已经有完整 sidecar 确认。

因为产品偏好已经固定为 `早发现优先`，所以：

- 只要共享 feed 已经形成明确对象，就允许先进 pool
- sidecar 负责后续加分、压噪与排序解释

## 6. 与 Snapshot 的关系

candidate pool 不是日报产物。

它的作用是：

1. 表达 Radar 接到了什么候选对象
2. 表达这些对象来自哪里
3. 为后续 `snapshot / Bark / 日报` 提供上游输入

进入 `snapshot` 之后，才进入：

- `runtime_state`
- `radar_bucket`
- `alert_level`
- `why_now / followup_path`

## 7. 当前实现口径

当前工作区已经先落下三层桥接：

1. `industry_signal_scan_latest.json`
2. `radar_opportunity_snapshot_latest.json`
3. `radar_daily_report_latest.md`

candidate pool contract 这一版先用于：

- 固定 mixed-object 输入语义
- 避免后续 live mixed-object 接入时重新发明字段
- 明确共享 feed 与 sidecar 的边界
