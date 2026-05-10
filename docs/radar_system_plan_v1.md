# Radar 系统规划 V1

## 0. 文档定位

这份文档是当前 `industry_signal_radar/` 工作区的 `Radar 系统规划总纲`。

它的职责是把当前工作区正式收敛为一个 `通用 Radar 机会发现系统` 的 V1 规划，并把现有的产品判断、上下游边界、输出 contract 和日报结构压成一份可直接落地的系统设计。

从现在开始，当前工作区的规划层建议按下面这套路由理解：

- `docs/radar_system_plan_v1.md`
  - 系统规划总纲
  - 定义系统定位、边界、V1 闭环、验收标准和默认假设
- `docs/radar_unified_plan.md`
  - 统一产品说明
  - 负责补充 Radar 的定位、角色分工和日度闭环语义
- `docs/radar_ranking_v1.md`
  - Ranking 方法文档
  - 定义候选池、排序目标、分桶、Bark 触发语义
- `docs/radar_output_contract_v1.md`
  - 输出 contract 文档
  - 定义 snapshot / Bark / 日报消费字段
- `docs/radar_daily_report_template.md`
  - 日报模板文档
  - 定义分桶面板与重点详解结构

`docs/execution_checklist.md` 继续保留为唯一执行主控文档，但不再承担系统总规划职责。

## 1. 一句话定义

`Radar` 是一个以 `News Event Hub` 为上游事件输入、以 `Research` 为下游研究承接、专注于把广义事件压成少量高价值研究优先级对象的 `机会发现系统`。

它不是新闻系统，也不是自动交易系统。它的核心问题不是“抓更多新闻”，而是：

`把广义事件压成少量值得先研究的对象。`

## 2. 近期目标与规划范围

当前这版系统规划只覆盖：

- `V1 可运行闭环`

也就是：

- shared feed 读取
- candidate pool 形成
- ranking
- Bark
- daily report
- replay / validation 基础能力

当前不优先展开：

- `News Event Hub` 的内部重构
- `Research` 的内部重构
- 6-12 个月长期全景蓝图
- 自动交易或收益预测系统

## 3. 系统边界与职责

### 3.1 News Event Hub

按 `外部依赖` 处理。

职责固定为：

- 新闻抓取
- normalize
- merge / cluster
- canonical mapping
- consumer exports

Radar 不在本轮规划里重建这些能力。

### 3.2 Radar

这是当前工作区的本体。

职责固定为：

- 读取共享 feed
- 叠加 Radar 自己的 sidecar
- 形成 `Ranking / Bucket / Alert Level / Follow-up Path`
- 输出 `snapshot / Bark / 日报 / 复盘数据`

### 3.3 Research

按 `下游承接层` 处理。

职责固定为：

- 接住 Radar 前排机会
- 升级为深度研究或持续跟踪对象
- 沉淀长期研究结论

所以系统关系固定为：

`News Event Hub -> Radar -> Research`

## 4. 当前默认产品偏好

`Radar V1` 当前锁定 3 条默认偏好：

### 4.1 早发现优先

- 默认优先发现更早期的潜在机会
- 不要求一开始就只保留高确定性晚信号
- 允许一定噪音，但必须保留研究入口价值

### 4.2 对象混排

V1 不再是单一行业榜。

允许进入同一张机会榜的对象类型包括：

- `industry`
- `macro`
- `company`
- `special_situation`
- `watchlist_priority_change`

### 4.3 分桶面板 + 少量重点详解

日报默认不是一张只有总分的榜，而是：

- 先分桶
- 再对少量前排对象展开

这既保留 `早发现优先` 的宽度，也保留人工判断所需的解释性。

## 5. V1 主闭环

V1 主闭环固定为：

`shared feed -> candidate pool -> ranking -> Bark -> daily report -> replay/validation`

具体语义如下：

1. 从 `News Event Hub` 读取共享 consumer exports
2. 形成 Radar 内部 candidate pool
3. 叠加 sidecar 信号并做排序
4. 对新增或升级到高优先级的对象触发 Bark
5. 生成 Radar 日报
6. 把排序和提醒结果沉淀为可复盘记录

## 6. V1 排序语义

### 6.1 排序目标

排序目标不是次日涨幅预测，而是：

`研究优先级`

也就是：

- 谁值得现在优先看
- 为什么现在看
- 它更像线索、候选还是强提醒
- 它有没有自然的 follow-up path

### 6.2 排序维度

V1 固定采用以下维度：

- `worth_watching`
- `why_now_strength`
- `confidence`
- `followup_value`
- `alert_level`

其中关键约束是：

- 当 `早发现` 和 `高确定性` 冲突时，默认适度向 `早发现` 倾斜
- 但必须用 `followup_value` 压制“高热度低研究价值”的对象

### 6.3 排序 bucket

V1 固定采用以下 bucket：

- `observe`
- `research_candidate`
- `strong_candidate`
- `strong_alert`

### 6.4 两套语义必须分开

当前工作区有两套相近但不同的语义：

- 运行状态机：`cold / warming / candidate / strong_alert`
- 排序 bucket：`observe / research_candidate / strong_candidate / strong_alert`

这两套语义在规划上明确视为并行概念，不合并。

## 7. V1 输出 contract

V1 每个排序对象的最小输出字段固定为：

### 身份字段

- `radar_object_type`
- `radar_object_id`
- `radar_object_name`
- `radar_object_scope`

### 排序字段

- `radar_bucket`
- `radar_score`
- `rank_in_bucket`
- `rank_overall`
- `alert_level`

### 维度字段

- `worth_watching`
- `why_now_strength`
- `confidence`
- `followup_value`

### 解释字段

- `why_now`
- `key_evidence`
- `supporting_events`
- `followup_path`
- `risk_flags`

### 状态字段

- `is_new`
- `is_upgraded`
- `previous_bucket`
- `trigger_state`
- `dedup_key`

### 研究连接字段

- `primary_symbols`
- `etf_proxies`
- `theme_overlays`
- `research_links`

当前默认解释是：

- `radar_score` 仅作为排序索引
- 不替代 `why_now / confidence / followup_path`

更具体的 schema 见：

- `docs/radar_output_contract_v1.md`

## 8. 日报与 Bark 规划

### 8.1 日报

日报固定为 `分桶面板 + 少量重点详解`，而不是泛新闻综述。

默认结构：

1. 顶部摘要
2. 分桶面板
3. 今日 Top Opportunities
4. 新增 / 升级 / 观察池
5. Bark 事件回顾
6. 附录

更具体模板见：

- `docs/radar_daily_report_template.md`

### 8.2 Bark

Bark 固定为更窄的输出面。

它只处理：

- 新进入 `strong_alert`
- 升级为 `strong_alert`

当前最小判断依赖：

- `radar_bucket` 或 `alert_level`
- `is_new / is_upgraded`
- `dedup_key`
- `why_now_strength / confidence / followup_value`

并明确：

- Bark 依赖 snapshot 字段
- 不从 Markdown 回读

## 9. 规划验收

这份系统规划完成后，应该能让实现者在不额外做产品决策的前提下回答：

- 上下游边界是什么
- Radar 的核心目标和非目标是什么
- V1 近期只实现到哪里
- 排序到底在排什么
- snapshot / Bark / 日报分别依赖哪些字段
- 排序 bucket 和运行状态机如何区分

## 10. 场景验收

V1 规划至少应支持下面这些场景：

1. 单个 `industry` 对象进入 `research_candidate`，只进日报不发 Bark
2. 一个对象从 `research_candidate` 升级到 `strong_alert`，触发 Bark 且日报进入前排
3. 一个 `macro` 或 `company` 对象与 `industry` 同时存在时，能进入同一张榜
4. 一个高热度但 `followup_value` 低的对象，不应进入前排或 Bark
5. 旧事件重复出现但无新增 `why_now` 时，应被压低或 suppress
6. 日报能直接从 snapshot 渲染，不需要人工重写排序语义

## 11. 文档一致性检查

后续执行时，至少要检查以下几类一致性：

- 对象类型一致
- bucket 名称一致
- Bark 触发条件一致
- 输出文件路径和命名一致
- `Hub 外部依赖 / Radar 本体 / Research 下游` 的角色定义一致

## 12. 当前默认假设

- `News Event Hub` 与 `Research` 按外部依赖处理，不改其内部设计
- 本轮系统规划只覆盖 `V1 可运行闭环`
- `industry` 仍是当前实现主体，但 contract 必须从一开始支持混排对象
- `radar_score` 只作为排序索引，不作为黑箱结论
- 运行状态机与排序 bucket 保持分离

## 13. 当前一句话判断

`Radar V1` 的本质不是新闻聚合器，而是一个把共享事件流压缩成研究优先级的机会发现系统。
