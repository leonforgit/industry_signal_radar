# Radar 统一主文档

## 0. 文档定位

这份文档是当前 `industry_signal_radar/` 工作区的 `统一产品说明文档`。

如果要看当前工作区最高优先级的系统总规划，优先参考：

- `docs/radar_system_plan_v1.md`

它统一回答 5 个问题：

- `Radar` 现在到底是什么
- 它和 `News Event Hub`、`Research` 的关系是什么
- 它每天要产出什么
- `Ranking` 大致应该按什么语义输出
- 后续实现应该先补哪几段闭环

从现在开始，这个工作区的文档路由建议固定为：

- `docs/execution_checklist.md`
  - 唯一执行主控文档
  - 负责阶段勾选、增量事实、阻塞和下一步
- `docs/radar_system_plan_v1.md`
  - 系统规划总纲
  - 定义系统定位、边界、V1 闭环、验收标准和默认假设
- `docs/radar_unified_plan.md`
  - 统一产品说明文档
  - 负责产品定位、输入输出、日报/Bark/Ranking 语义
- `docs/radar_ranking_v1.md`
  - Ranking 方法文档
  - 负责候选池、排序维度、分桶和 Bark 触发语义
- `docs/radar_output_contract_v1.md`
  - 输出 contract 文档
  - 负责 snapshot / 日报 / Bark 的字段定义
- `docs/radar_daily_report_template.md`
  - 日报模板文档
  - 负责分桶面板与重点详解结构
- 其他 `docs/*.md`
  - 专项说明文档
  - 负责对象层、成功标准、可行性、contract、专题节点设计

`docs/master_plan.md` 继续保留，但从现在开始只视为 `早期背景规划文档`，不再作为当前主线入口。

## 1. 一句话定位

`Radar` 是一个从共享新闻/事件底座拉取输入、再叠加自身 sidecar 信号做二次发现与排序的 `机会发现器`。

它不是新闻真相源，不负责重建完整新闻系统；它的职责是：

- 每天从共享 feed 中读取候选
- 用 Radar 自己的多层证据重新排序
- 产出 `Bark 告警 + 日度机会排序 + 投资机会报告`

当前这个仓库虽然物理名称仍是 `industry_signal_radar`，但产品语义已经提升为更通用的 `Radar` 实现位。

## 1.1 当前已锁定的默认产品偏好

当前产品方向已经额外锁定以下偏好：

- `早发现优先`
  - 默认优先更早发现潜在机会，而不是只保留高确定性晚信号
- `对象混排`
  - `industry / macro / company / special_situation / watchlist_priority_change` 可以进入同一张机会榜
- `分桶面板 + 少量重点详解`
  - 日报默认按分桶组织，再展开少量最重要对象

## 2. 系统角色分工

### News Event Hub 负责

- 新闻抓取
- article normalize
- event merge / cluster
- canonical entity mapping
- consumer exports

### Radar 负责

- 读取共享事件输入
- 叠加自身 sidecar 信号
- 把广义事件压成 `值得先看的对象`
- 形成 `Ranking / Bucket / Alert Level / Follow-up Path`
- 产出日度报告和实时提醒

### Research 负责

- 接住 Radar 给出的高价值候选
- 把少量高优先级对象升级成真正的深度研究
- 沉淀成长期研究结论和机会跟踪

所以三者关系应该是：

`News Event Hub -> Radar -> Research`

而不是让 Radar 自己去兼任新闻系统和深度研究系统。

## 3. 当前产品目标

当前 Radar 的目标不是“抓到最多新闻”，而是每天回答下面这些问题：

1. 今天最值得我先看的投资机会是什么
2. 为什么是这些对象，而不是别的对象
3. 哪些只是线索，哪些已经值得立刻研究
4. 哪些需要实时 Bark，哪些只需要进入日报排序

对应到产物层，v1 至少要稳定产出：

- `实时 / 准实时 Bark 告警`
- `日度机会排序快照`
- `日度投资机会报告`
- `可复盘的事件与排序记录`

## 4. 当前非目标

当前阶段明确不把这些事情当成 Radar 的主任务：

- 自己重建完整新闻采集系统
- 一开始就做成机构级全量实时情报平台
- 直接输出自动交易指令
- 只靠一个总分黑箱决定全部机会
- 把所有新闻都直接变成“投资机会”

## 5. 日度运行闭环

当前建议把 Radar 的日常闭环固定成下面 6 步：

1. 从 `News Event Hub` 读取最新 consumer feeds。
2. 把共享 feed 中的对象、事件、候选信号压成 Radar 内部候选池。
3. 叠加 Radar 自己的 sidecar：价格、资金、公告、proxy、代表股 / ETF / overlay、研究上下文。
4. 对候选池做 `Ranking + Bucket + Alert Level`。
5. 对满足阈值的新增 / 升级对象发送 Bark。
6. 生成当日 `投资机会报告`，把 Bark 告警和排序结果一起沉淀下来。

这意味着 `Bark` 和 `日报` 不是两条分裂的产品线，而是同一个 Radar 排序系统的两种输出面。

## 6. Ranking 的 v1 语义

更具体的方法定义，见：

- `docs/radar_ranking_v1.md`

当前我们还不应该过早写死复杂公式，但应该先把 `Ranking 输出语义` 固定下来。

### 6.1 排序对象

V1 的排序对象建议至少支持：

- `industry`
- `macro`
- `company`
- `special_situation`
- `watchlist_priority_change`

当前仓库依然以 `industry` 为主，但排序 contract 不应再被行业单一对象限制死。

### 6.2 每个候选至少要回答

- 它是谁
- 为什么现在值得看
- 它属于什么类型的机会
- 证据来自哪些层
- 当前更像观察、候选，还是强提醒
- 下一步研究动作是什么

### 6.3 建议的 v1 排序维度

v1 可以先按 5 个维度表达，而不是先追求精确权重：

1. `shared_event_strength`
   - 上游共享事件本身的强度、密度和新鲜度
2. `cross_signal_resonance`
   - Radar sidecar 是否给出资金、价格、公告、proxy 的共振确认
3. `evidence_quality`
   - 来源质量、可解释性、是否有明确证据链
4. `actionability`
   - 是否能自然进入下一步研究，是否存在清晰 follow-up path
5. `confidence`
   - 当前整体判断更偏线索、候选，还是高置信机会

### 6.4 建议的 v1 排序输出字段

日报排序和快照输出建议至少包含：

- `radar_object_type`
- `radar_object_id`
- `radar_object_name`
- `radar_score`
- `radar_bucket`
- `alert_level`
- `confidence`
- `why_now`
- `key_evidence`
- `supporting_events`
- `followup_path`
- `generated_at`

其中：

- `radar_score`
  - 用来做日度排序
- `radar_bucket`
  - 用来表达这是什么类型的机会
- `alert_level`
  - 用来决定 Bark 是否触发
- `followup_path`
  - 用来决定研究动作，而不是只给分不告诉你怎么跟

### 6.5 v1 不急着定死的东西

下面这些内容当前可以继续迭代，不必在这轮先写死：

- 精确数值权重
- 单一统一总分公式
- 所有对象类型完全一致的打分模板
- 最终阈值

这一阶段更重要的是：`先让排序结果可读、可解释、可复盘`。

## 7. Bark 与报告的关系

### Bark 的职责

Bark 只负责：

- 新触发提醒
- 候选升级到强提醒
- 必要时的简短日终摘要

它默认不承担完整研究说明书的职责。

### 日报的职责

日报负责：

- 汇总当天最值得看的机会
- 给出完整的排序与分桶
- 承接 Bark 未展开的信息
- 明确下一步研究动作

因此产品口径应该是：

- `Bark = 实时/准实时提醒面`
- `Radar Daily Report = 当日机会面板与研究入口`

## 8. Radar 日报建议结构

你提到的 `Research/opportunities/daily` 很适合作为 `报告组织语义` 参考，但 Radar 日报应该更偏“排序与研究入口”，而不是泛新闻日报。

当前建议的 Radar 日报结构如下：

### 1. 顶部摘要

- 当天扫描时间
- 候选总数
- 新增高优先级机会数
- Bark 触发数
- 需要继续跟踪的对象数

### 2. 分桶面板

优先分成：

- `strong_alert`
- `strong_candidate`
- `research_candidate`
- `observe`

每个 bucket 内部再按排序输出。

### 3. 今日 Top Opportunities

用一张核心表回答：

- 对象
- 类型
- 排名
- `radar_score`
- `alert_level`
- 主要证据
- 为什么现在看
- 下一步研究动作

### 4. 新增 / 升级 / 观察池

把对象分成：

- `新增重点`
- `升级重点`
- `继续观察`
- `降级或移出`

### 5. Bark 事件回顾

列出当天实际触发的 Bark：

- 触发时间
- 对象
- 触发原因
- 告警级别

### 6. 候选详解

对前几名机会给出简洁但够用的说明：

- 机会定义
- 共振层
- 关键证据
- 失真风险
- 建议 follow-up path

### 7. 附录

附录只放必要的运行与质量信息：

- 关键 source health
- 主要未决问题
- 需要明天继续观察的对象

## 9. 与 Research 目录机会报告的关系

Radar 日报应参考 `Research/opportunities/daily/reports/` 的组织感，但不要直接退化成那种“广泛新闻汇总”的写法。

更合理的分工是：

- `Research` 日报
  - 可以更宽
  - 可以承接宏观、公司、传闻、跟踪新闻
- `Radar` 日报
  - 必须更窄
  - 只保留排序后真正有投资入口价值的对象

也就是说，Radar 不是“再写一份新闻摘要”，而是“把广泛事件压成少量高价值机会”。

## 10. 推荐输出落点

具体字段 contract 和日报模板，分别见：

- `docs/radar_output_contract_v1.md`
- `docs/radar_daily_report_template.md`

当前建议把 Radar 的主输出固定到这些位置：

- `output/reports/`
  - 日报主稿与 latest alias
- `output/snapshots/`
  - 排序快照、候选 JSON、Bark 摘要
- `reports/main.md`
  - durable 结论，不放每日流水

建议的命名口径可以先这样约定：

- `output/reports/radar_daily_report_YYYYMMDD.md`
- `output/reports/radar_daily_report_latest.md`
- `output/snapshots/radar_opportunity_snapshot_YYYYMMDD.json`
- `output/snapshots/radar_opportunity_snapshot_latest.json`

## 11. 当前实现优先级

基于现在的项目状态，后续更合理的优先级应当是：

1. 固定 `shared feed -> Radar candidate -> Ranking -> Bark -> 日报` 这条主闭环。
2. 固定 `Ranking` 的输出 contract，而不是先追求复杂公式。
3. 固定 Radar 日报模板，确保它真的是“投资机会报告”。
4. 继续优化新闻层分流、去重、聚类和映射质量，避免排序入口被噪音污染。
5. 继续扩行业 proxy / 资金 / 公告层，提升排序的解释力和跟踪价值。

## 12. 当前一句话判断

这个工作区后续不应该再被理解成“行业新闻扫描器”。

它更准确的定位是：

`一个以共享新闻事件为输入、以机会排序和研究入口为输出的通用 Radar 系统。`
