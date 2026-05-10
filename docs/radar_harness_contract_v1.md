# Radar Harness Contract v1

本文档冻结 Radar / Harness 当前的生产合同，避免继续用“不断 review、不断补洞”的方式演化系统。

## 目标

Radar Harness 只回答三件事：

- 今天能不能生产并投递一份 PM 可用的雷达事件作战单；
- 如果不能，失败发生在哪个可验证节点；
- 如果可以自动补救，哪个 Agent 任务负责修复，修复结果由什么命令验收。

## 状态合同

- `pass`：当前关键产物满足生产合同，可以发布或继续投递链。
- `warn`：生产链没有硬失败，但存在必须展示到健康账本或任务队列的降级。
- `fail`：生产链不可发布，日报、PDF 或邮件链必须阻断。
- `action_required`：Agent task queue 有待处理任务；它不是日报本身的失败状态。

顶层 manifest 的 `status / production_status` 表示当前关键产物健康，而不是只能变坏的历史状态。完整 workspace build 的原始结果保存在 `workspace_status / workspace_production_status`。

## Artifact Health

`scripts/radar_current_health.py` 是当前健康 reconciler，必须满足：

- 绑定并记录关键产物的 `status / generated_at / run_id / sha256 / mtime`；
- 任何关键产物 `fail` 或存在 blocker 时，current health 为 `fail`；
- 任何关键产物 `warn`、有 warning，或产物晚于 harness manifest 时，current health 为 `warn`；
- 当关键产物从 `fail` 恢复为 `pass/warn` 后，manifest 顶层状态必须随当前健康恢复，不能永久停留在旧 fail；
- workspace build 的历史状态只作为审计字段保留，不能覆盖当前健康。

## Kimi Research Contract

Kimi research harness 是生产必需节点：

- `pass` 必须覆盖全部主 verdict，且 `model_generated` 覆盖率为 100%；
- `partial_pass / deterministic_fallback / disabled / skip_no_credentials / fail` 都不能作为干净 pass 放行；
- IPO 与结构性 sidecar verdict 必须校验字段、决策、权重、provenance、`model_generated`、可执行 `next_action` 和 coverage；
- `risk_flags` 必须是结构化对象，不能退回字符串；
- `risk_flags.next_action.check` 不能包含“核实真实性”“继续关注”“定位风险来源”“确认是否影响前排对象”等泛动作。

## Agent Task Queue Contract

`scripts/build_radar_agent_task_queue.py` 是可执行 Harness 状态机，不是 backlog 文件。

允许状态：

- `ready`
- `claimed`
- `running`
- `blocked`
- `done`

必需不变量：

- claim/start/complete/block/release 都必须持有 queue lock；
- `done` 必须有 result artifact，且 validation commands 全部退出 `0`；
- `complete` 必须在锁外执行 repair/validation，再用 task id、trigger hash、claim owner 做 apply 校验；
- `lease_expires_at` 必须覆盖 repair + validation 的完整执行预算；
- parent auto-worker timeout 必须覆盖 repair + validation 的完整执行预算；
- expired lease 必须自动回收或标记 blocked；
- 同一稳定根因只能生成一张任务，动态 trigger 作为当前证据刷新，不能制造重复任务；
- 派生 P0/P1 不能阻断被标记为 root-cause 的 P2 自动修复任务。

## Delivery Contract

正式投递链必须记录：

- invocation source；
- systemd invocation id；
- timer last trigger；
- dry-run / smtp-required；
- delivery stage；
- snapshot / quality / source readiness / report artifact hash；
- run_id。

`latest` 手动重跑不能抹掉最近一次 scheduled timer 结果；scheduled 结果必须单独保留。

## Source Readiness Contract

source readiness 不只看文件 freshness，还必须读取 News Event Hub 的 source health：

- 核心源 down 是 blocker；
- 非核心源大量 down/degraded 至少是 `warn`；
- News Event Hub feed stale 是 blocker；
- source readiness 的 `warn/fail` 必须进入 report quality 和 task queue。

## Current Findings Ledger

| ID | Priority | Finding | Current state | Resolution |
| --- | --- | --- | --- | --- |
| 1 | P1 | current health 只能变坏不能恢复 | fixed | `radar_current_health.py` 现在把顶层状态设为当前 artifact health，并保留 `workspace_status` 审计字段；smoke 覆盖 stale fail 恢复为 current warn。 |
| 2 | P1 | complete lease 没覆盖 repair 阶段 | fixed | `prepare_complete_task` 的 lease budget 覆盖 repair + validation。 |
| 3 | P2 | auto-worker 超时预算漏算 repair | fixed | auto-worker parent timeout 使用 repair + validation 命令总数。 |
| 4 | P2 | P0/P1 阻断会卡住根因自动修复 | fixed | task 支持 `auto_run_with_active_blockers`，root-cause P2 可在派生 P0 存在时自动运行。 |
| 5 | P2 | risk_flags 泛动作仍能过 validator | fixed | `validate_radar_kimi_research.py` 对 risk flag next_action 也执行 BAD_ACTION_TEXT 拦截。 |
| 6 | P3 | 队列任务去重键过细 | fixed | task 支持稳定 `dedupe_key`，同一节点/根因不会因 trigger 文本变化生成多张任务。 |
| 7 | P1 | IPO/结构性 verdict 没有被 validator 实质校验 | fixed | `validate_payload` 已校验 `ipo_verdicts / structural_verdicts` 和 `sidecar_coverage`。 |
| 8 | P1 | Agent task queue 仍不是可执行 Harness 状态机 | fixed | queue 已支持 claim/start/complete/block/release、lease、lock、result artifact 和 validation。 |
| 9 | P2 | successful recovery 不会被识别为研究降级 | fixed | quality gate 与 task queue 均识别 `successful_recoveries / structural_repair / sidecar_repair / operational_flags`。 |
| 10 | P2 | warning_count 可能被空 warnings 覆盖掉 | fixed | `summarize_output` 取显式 `warning_count` 与 `len(warnings)` 的最大值。 |
| 11 | P1 | 任务可被无验证地标记为 done | fixed | `done` 需要 validation results 全部 pass，否则 blocked。 |
| 12 | P2 | task queue 状态文件没有并发锁 | fixed | queue update 使用 `state/radar_agent_task_queue.lock`。 |
| 13 | P2 | lease_expires_at 被写入但不会自动回收 | fixed | queue rebuild 和 actions 都会 reclaim expired leases。 |
| 14 | P3 | 研究告警任务颗粒度偏噪音化 | fixed | research warnings 按 harness label 聚合。 |
| 15 | P1 | complete 持锁执行可重入验证命令 | fixed | CLI complete 已改为两阶段，锁外执行 repair/validation，smoke 覆盖 reentrant validation。 |
| 16 | P2 | smoke 没覆盖 CLI 持锁验证路径 | fixed | `smoke_test_radar_harness_v2.py` 覆盖 CLI claim/complete 与 reentrant queue builder。 |
| 17 | P1 | sidecar coverage 可以被空 verdict 列表伪造 | fixed | validator 现在把 `sidecar_coverage` 与实际 `ipo_verdicts / structural_verdicts` 的 model_generated 数量交叉校验；smoke 覆盖空 verdict 伪造 coverage。 |
| 18 | P2 | current health 会吞掉显式 warning_count | fixed | `radar_current_health.py` 现在取显式 `warning_count` 与 `len(warnings)` 的最大值；smoke 覆盖 `warning_count>0 / warnings=[]`。 |
| 19 | P1 | sidecar 可用对象会被 `required_count=0` 藏掉 | fixed | pass payload 只要 `available_count>0` 就必须有 required sidecar verdict；validator 和 smoke 覆盖 `available_count=1 / required_count=0` 的伪 pass。 |
| 20 | P1 | critical artifact 缺 status 或显式 blocker/failure 仍可 pass | fixed | current health 对缺失/未知 status、`blocker_count>0`、`failure_count>0` 一律降为 fail；smoke 覆盖这三类 malformed critical artifact。 |
| 21 | P1 | 价格 freshness 把开盘后误当成当日完整价格样本可用 | fixed | 增加 `market_sample_ready_time`，候选池和市场/源 readiness 在 cutoff 前使用上一完整交易日；候选池优先使用价格底座最新日期作为 `market_sample_date`，smoke 覆盖 15:37/18:30 cutoff。 |
| 22 | P1 | 非核心上游源 warn 会被日报质量门禁当成硬阻断 | fixed | quality gate 现在只在 source readiness/source health 有 blockers、fail 或 critical_down 时阻断；非核心 down/degraded 保持 warn 并进入任务队列。 |
| 23 | P2 | 价格底座全量刷新遇到重复 `(instrument, trade_date)` 会写库失败 | fixed | `build_equity_price_substrate.py` 写入前按 `instrument/trade_date` 去重，远端 A 股 builder 已验证不再触发 UNIQUE constraint。 |
| 24 | P1 | Kimi research 节点缺少外层硬 timeout，端到端 dry-run 可长时间无 artifact 更新 | fixed | workspace harness 对 Kimi editorial/research 命令加节点 timeout，timeout 会写入 step manifest。 |
| 25 | P2 | sentiment freshness 仍按开盘时间计算期望样本日 | fixed | `ensure_radar_sentiment_freshness.py` 改用 `market_sample_ready_time`，与 source readiness/market sample 语义一致。 |
| 26 | P1 | Kimi HTTP 调用可卡在底层 socket/poll，Python signal 不一定能打断 | fixed | Kimi API call 移入独立子进程，父进程按 `timeout_seconds` terminate/kill；smoke 覆盖慢 HTTP 必须硬超时。 |
| 27 | P1 | Kimi 大 prompt 被 30 秒 socket timeout 误杀 | fixed | `urlopen` socket timeout 改为真实模型预算；外层子进程 timeout 负责兜底。 |
| 28 | P1 | IPO sidecar 把 HKEX ETF 新上市当成股票打新必填对象 | fixed | HK IPO watchlist 过滤 ETF/covered-call 非股票上市，A/H IPO 研究只覆盖股票申购对象。 |
| 29 | P1 | IPO sidecar key 未归一导致 `07630.HK` 与 `07630` 被误判缺失/重复 | fixed | Kimi sidecar coverage 和 dedupe 对 `.HK/.SZ/.SH/.BJ` 后缀做规范化，smoke 覆盖。 |
| 30 | P2 | Kimi research 长跑缺进度产物 | fixed | Kimi harness 写 `output/runs/radar_kimi_research_progress_latest.json`，记录 candidate shard/sidecar 阶段。 |
| 31 | P1 | data substrate audit 把非阻断 warn 当成 fail | fixed | 仅 source blockers/fail、price fail、fundamental fail 阻断；source/price warn 写入 findings 并返回 rc=0。 |
| 32 | P1 | 邮件 source readiness gate 把 warn 当成跳过投递 | fixed | 邮件脚本只在 source readiness fail 或存在 blockers 时跳过；warn 随质量门禁进入邮件正文和 delivery manifest。 |

## Acceptance Commands

最小验收：

```bash
python3 -m py_compile scripts/radar_current_health.py scripts/build_radar_workspace_outputs.py scripts/build_radar_agent_task_queue.py scripts/validate_radar_kimi_research.py scripts/smoke_test_radar_harness_v2.py
python3 scripts/smoke_test_radar_harness_v2.py
python3 scripts/validate_radar_kimi_research.py
bash -n scripts/run_radar_daily_report_email.sh
```

远端生产验收还应检查：

```bash
systemctl cat industry-signal-radar-daily-report.service
systemctl list-timers industry-signal-radar-daily-report.timer --no-pager
```
