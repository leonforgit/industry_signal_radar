# Radar Ranking V1

## 0. 文档定位

这份文档定义 `Radar` 的 `Ranking v1` 方法框架。

如果要看当前工作区最高优先级的系统总规划，优先参考：

- `docs/radar_system_plan_v1.md`

它要解决的问题不是“写出一个完美总分公式”，而是先回答：

- 什么样的对象应该进入 Radar 候选池
- Radar 应该按什么语义排序
- 什么对象只进日报，什么对象应该触发 Bark
- 后续如何验证这个排序是否真的对投资流程有帮助

当前阶段，`Ranking v1` 的优先级是：

1. 可解释
2. 可复盘
3. 可迭代
4. 公式简洁

而不是一开始就追求复杂建模。

## 1. 一句话目标

`Ranking v1` 要做的事情，是把广义新闻/事件候选压成一张“今天最值得先看的对象优先级列表”。

这个列表服务的是：

- 注意力分配
- 研究排队
- 实时提醒

它不是收益预测器，也不是自动交易打分器。

## 1.1 当前已锁定的产品偏好

基于当前项目决策，`Ranking v1` 默认采用以下 3 条偏好：

1. `早发现优先`
   - 排序默认偏向更早发现潜在机会
   - 允许一定噪音，但不能牺牲研究入口价值
2. `对象混排`
   - `industry / macro / company / special_situation / watchlist_priority_change` 可以进入同一张日榜
   - 不再把 Radar 限制为单一行业榜
3. `分桶面板 + 少量重点详解`
   - 日报默认不是一张只有总分的榜
   - 而是先分桶，再对少量重点对象做展开

## 2. 排序目标定义

当前 Radar 排序的真正目标，不是“谁最可能明天涨”，而是：

`谁最值得你现在优先投入研究注意力。`

这意味着一个对象的排序高低，应该同时受 4 类因素影响：

1. 它现在是不是有新东西发生
2. 这些变化是不是有跨层共振
3. 这些证据是不是足够可信、足够可解释
4. 你现在去看它，是否真的有研究价值和行动价值

在当前版本里，这个目标还要额外满足：

- 当 `早发现` 和 `高确定性` 冲突时，默认适度向 `早发现` 倾斜
- 但不能把“只有热度、没有研究抓手”的对象顶到最前面

## 3. 候选池定义

### 3.1 候选池来源

Radar 的初始候选池来自两部分：

- `News Event Hub` 的共享 consumer feeds
- Radar 自己的 sidecar 异动和补充线索

### 3.2 候选池准入规则

一个对象进入当日候选池，至少满足下列条件之一：

1. 共享 feed 给出 `opportunity candidate`
2. 共享事件在当日出现明显聚集或显著更新
3. Radar 自己的资金/公告/proxy/价格层出现明显异常
4. 已在观察池中的对象出现升级信号

### 3.3 候选池不应纳入的对象

当前阶段应尽量排除：

- 只有噪音标题，没有明确对象承载的新闻
- 只有单条低质量转载，没有后续支撑的事件
- 只有宏观背景、但没有具体研究入口的泛叙事
- 明显重复、无新增信息的旧事件

## 4. 排序对象

`Ranking v1` 建议支持以下对象类型：

- `industry`
- `macro`
- `company`
- `special_situation`
- `watchlist_priority_change`

当前默认方向是 `对象混排`：

- `industry` 仍会是最主要的一类
- 但 `macro / company / special_situation / watchlist_priority_change` 也应允许进入同一张排序面板
- 后续实现可以分阶段接入，但 contract 不再按“行业专用榜”设计

## 5. 排序框架

### 5.1 不直接只做一个总分

`Ranking v1` 不建议一开始只输出单一总分。

更好的做法是先输出 5 个可解释维度，再合成一个简化 `radar_score`：

1. `worth_watching`
2. `why_now_strength`
3. `confidence`
4. `followup_value`
5. `alert_level`

同时，从事件驱动的产品定义出发，当前 v1 还需要显式补出一组 `catalyst context` 字段：

6. `catalyst_type`
7. `hard_or_soft`
8. `catalyst_stage`
9. `next_milestone`
10. `milestone_due_window`
11. `event_driven_lane`
12. `confirmation_gap`
13. `hedge_difficulty`
14. `failure_mode`
15. `evidence_quality`
16. `triage_action`

### 5.2 五个核心字段

#### A. `worth_watching`

回答：

- 这个对象今天值不值得进入你的视野

它更像一个总的“值得看程度”。

#### B. `why_now_strength`

回答：

- 为什么偏偏是现在看
- 今天相对昨天是否有明显增量

它强调的是 `时点性`，不是长期质量。

在当前版本里，这一维会天然更重要，因为系统默认偏向 `早发现优先`。

#### C. `confidence`

回答：

- 当前证据链到底有多扎实
- 是早期线索，还是已有较强确认

它强调的是 `置信度`。

但在当前版本里，它不应把所有“早期但值得盯”的对象都压下去。

#### D. `followup_value`

回答：

- 这个对象现在能不能自然进入下一步研究
- 有没有明确 follow-up path

这是很关键的一维，因为很多“看起来热”的东西并不具备研究入口价值。

在 `早发现优先` 的框架下，这一维尤其重要，因为它决定我们是否能容忍更早期的信号进入前排。

#### E. `alert_level`

回答：

- 它只该出现在日报里
- 还是已经值得发 Bark

这是 `排序输出` 和 `提醒输出` 之间的桥梁。

#### F. `catalyst_type`

回答：

- 这到底是哪一类催化剂

它是把对象从“有新闻”推进到“有事件语义”的第一步。

#### G. `hard_or_soft`

回答：

- 这是有明确 roadmap 的 `hard catalyst`
- 还是更依赖研究推演的 `soft catalyst`

#### H. `catalyst_stage`

回答：

- 这个催化剂当前处在 `announced / execution_window / confirmation_window / signal_clustered / monitoring / active_risk` 的哪一档

#### I. `next_milestone`

回答：

- 下一步最该盯什么

#### J. `milestone_due_window`

回答：

- 这个下一步大致在多短的窗口里应该看到

#### K. `event_driven_lane`

回答：

- 这个对象应不应该进入 `hard catalyst board`
- 还是进入 `soft catalyst watchlist`
- 或者暂时只进 `risk monitor / background monitor`

#### L. `confirmation_gap`

回答：

- 现在还缺哪一层确认
- 这个对象为什么还不能更激进地往前排

#### M. `hedge_difficulty`

回答：

- 如果要做风险管理，这个对象大概好不好对冲

#### N. `failure_mode`

回答：

- 这条逻辑最可能怎么失败
- 失败时更像“叙事落空”还是“映射错位”还是“兑现不及预期”

#### O. `evidence_quality`

回答：

- 当前证据更像 `structured_confirmed`
- 还是 `proxy_confirmed`
- 还是更早期的 `early_thematic`

#### P. `triage_action`

回答：

- 现在应该立刻进入研究
- 继续 thesis watch
- 先做风险复核
- 还是只保留背景监控

## 6. 建议的具体维度

### 6.1 `worth_watching`

可以由下面几类信息综合形成：

- 共享事件强度
- 共振层数
- 新增信息密度
- 对象在当日候选中的相对突出程度

### 6.2 `why_now_strength`

重点看：

- 是否是当日新增
- 是否是当日升级
- 是否出现跨源确认
- 是否出现价格/资金/公告/proxy 的新增确认

### 6.3 `confidence`

重点看：

- 来源质量
- 证据是否跨层
- 证据是否相互一致
- 是否存在明显失真风险

### 6.4 `followup_value`

重点看：

- 有没有清晰的研究抓手
- 有没有可继续跟踪的代表标的、proxy、公告、政策、公司
- 是不是能自然导向下一步研究问题

### 6.5 `alert_level`

重点看：

- 当前是 `观察`
- `候选`
- `强提醒`

这层不等于简单分数阈值，还要考虑：

- 是否新增
- 是否升级
- 是否已经在 suppress window 里

## 7. 分桶体系

`Ranking v1` 建议至少把对象分成下面几类：

### `observe`

- 有线索
- 但证据还薄
- 适合进日报观察池
- 不适合 Bark

这是 `早发现优先` 策略下非常重要的缓冲层。

### `research_candidate`

- 已经具备较明确的研究入口
- 值得在日报里排到前面
- 视情况可进入 watchlist / 跟踪池

这会是当前 Radar 日报里最核心的一层。

### `strong_candidate`

- 多层共振更明确
- 证据和行动价值都更强
- 已经接近 Bark 的下沿

### `strong_alert`

- 明显新增或明显升级
- 具备跨层证据
- 值得实时或准实时提醒

这个分桶比“只有一个总分”更适合当前工作流，尤其适合你已经明确偏好的 `分桶面板 + 少量重点详解`。

## 8. `radar_score` 的角色

在 `Ranking v1` 中，`radar_score` 只是排序工具，不是最终语义本身。

它的职责是：

- 在同一 bucket 内部做排序
- 在日报中输出可比较顺序

它不应该替代：

- `why_now`
- `confidence`
- `followup_path`
- `alert_level`

所以 `radar_score` 当前建议被理解为：

`排序索引，而不是黑箱结论。`

## 9. Bark 触发逻辑

### 9.1 Bark 不是给高分对象全发

Bark 触发必须比日报更严格。

它只适合处理：

- 新进入 `strong_alert`
- 从 `research_candidate / strong_candidate` 升级为 `strong_alert`
- 日终摘要中少量最重要对象

### 9.2 Bark 最低条件

建议 v1 至少同时满足：

- `why_now_strength` 不低
- `confidence` 不低
- `followup_value` 不低
- 对象处于新增或升级状态

### 9.3 Bark 不应触发的情况

- 只是高热度，但没有 follow-up path
- 只是旧事件重复出现
- 只是单源传闻，没有跨层支撑
- 分数高，但并没有新的“why now”

## 10. 日报输出逻辑

日报不是 Bark 的堆叠版。

日报建议覆盖：

- Top ranked objects
- 新增重点
- 升级重点
- 观察池
- Bark 当日回顾

日报比 Bark 更宽，但必须仍然是“投资机会报告”，而不是泛新闻列表。

当前默认结构应理解为：

- 第一层：分桶面板
- 第二层：少量重点详解

而不是把所有对象都写成长解释。

## 11. v1 最小输出 contract

建议每个排序对象至少输出：

- `radar_object_type`
- `radar_object_id`
- `radar_object_name`
- `radar_bucket`
- `radar_score`
- `worth_watching`
- `why_now_strength`
- `confidence`
- `followup_value`
- `alert_level`
- `why_now`
- `key_evidence`
- `supporting_events`
- `followup_path`
- `generated_at`

## 12. v1 的实现原则

### 原则 1

先做规则型、可解释型排序，不急着做复杂模型。

### 原则 2

先把 `bucket + why_now + followup_path` 做清楚，再继续调 `radar_score`。

### 原则 3

先把排序结果做成你愿意每天看的一张榜，再谈数学最优。

### 原则 4

先允许一定误报，但不能让结果失去研究入口价值。

### 原则 5

在 `早发现优先` 的前提下，优先保留“值得继续跟”的对象，而不是只保留已经高度确认的对象。

## 13. v1 的验证方法

当前排序是否有效，不应只看“后面涨没涨”，而应至少看 4 件事：

1. 排名前列对象是否明显更值得你点开看
2. 排名前列对象是否更容易进入后续研究
3. Bark 是否比日报更窄、更准
4. 一段观察期后，系统是否真的改变了你的研究顺序

建议的 v1 观察指标：

- `top_rank_open_rate`
- `top_rank_followup_rate`
- `bark_actionable_rate`
- `late_but_useful_rate`
- `obvious_noise_rate`

## 14. 当前最难的部分

`Ranking v1` 最大的难点不是打分公式，而是下面 3 件事：

1. 定义什么叫“值得优先研究”
2. 处理“早发现”和“高确定性”的冲突
3. 把“高热度但低研究价值”的对象排下去

所以这份文档的目的，就是先把这 3 件事的语义固定住。

## 15. 当前一句话判断

`Ranking v1` 的核心不是预测涨跌，而是把共享事件流压缩成可解释、可行动、可提醒的研究优先级。
