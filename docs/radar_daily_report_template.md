# Radar Daily Report Template V1

## 0. 文档定位

这份文档定义 `Radar 日报` 的默认模板。

如果要看当前工作区最高优先级的系统总规划，优先参考：

- `docs/radar_system_plan_v1.md`

当前模板遵循已确认的产品偏好：

- `早发现优先`
- `对象混排`
- `分桶面板 + 少量重点详解`

它的目标不是做泛新闻摘要，而是生成一份：

`真正能帮助你决定今天先研究什么的投资机会报告。`

当前默认呈现形态是 `morning_brief`：正式日报应控制在晨会前可读的一到两页。完整分桶、质量检查、源健康、长附录和原始对象列表留在 `handoff / snapshot / quality / sidecar`，不默认塞进主报告。

## 1. 顶部 Front Matter

建议日报默认带上输出标记：

```yaml
---
codex_output: true
codex_output_category: "radar_daily_report"
codex_output_entity: "radar_workspace"
codex_output_title: "Radar 投资机会日报 YYYY-MM-DD"
---
```

## 2. 标题区

```md
# Radar 投资机会日报

截至 YYYY-MM-DD | 运行批次 RUN_ID | 市场时区 Asia/Shanghai
```

## 3. 顶部摘要

这一节只回答最重要的总览问题。

建议摘要卡片或摘要行只保留会影响今天动作的项目：

- 候选 / 分流计数
- 当日 Bark 触发数
- 市场级资金流、事件情绪和综合情绪
- source readiness / 价格补数 / 新闻补核状态
- 今日最值得优先研究的 2-3 个对象

建议示例：

```md
## 一、顶部摘要

- 本轮共生成 `18` 个 Radar 候选，其中 `strong_alert 2` 个，`strong_candidate 4` 个，`research_candidate 7` 个，`observe 5` 个。
- 当日实际触发 Bark `2` 条，主要集中在 `industry + company` 两类对象。
- 当前最值得优先研究的方向集中在 `航运 / 电网 / 某公司事件`。
```

## 4. 事件驱动作战板

这部分是日报主体，不再默认展示完整 bucket。只保留：

- `Hard Catalysts`
- `Soft Catalyst Watchlist`
- `Risk Monitor`

每个板块默认 2-3 条。仍缺最新公司价格的对象不进入主报告前台，只在价格补数 warning 中披露。

## 5. Kimi 初研 / IPO

这部分承接 Kimi 和 IPO watchlist。

- `Kimi 初步研究`：如果 Kimi 可用，给出机会雏形、初判和今天最该补的验证动作；如果不可用，只披露降级状态。
- `A/H IPO 打新申购初筛`：只展示仍在申购窗口或即将申购的新股；先用申购期、发行价、发行 PE / 行业 PE、募资用途、基石/保荐人、公开认购热度做申购价值初筛。已上市或申购已截止的对象不作为今日打新机会。

## 6. 今日 Top Opportunities

这部分只保留少量最值得展开的对象。

建议数量：

- 默认 `2-3` 个

建议每个对象的展开结构：

```md
### 1. 对象名 | 类型 | bucket

- Why now：一句话说明为什么今天看
- 市场反应：最新目标交易日价格、涨跌和量能；缺失则不进主报告 Top
- 核心证据：列 1-2 条
- 今日动作：1 条
- 主要缺口：1 条
```

这里的重点不是写成长文，而是让你快速决定：

- 要不要点开继续看
- 应该先沿哪条线继续跟

## 7. 明日跟踪

只列 2-3 个需要延续跟踪的对象，避免把日报扩写成全量观察池。

## 8. 候选详解的写法约束

当前日报不要写成“全面新闻综述”。

每个详解对象默认只回答：

1. 为什么今天看
2. 哪些层在共振
3. 下一步怎么研究
4. 当前最主要的不确定性是什么

不建议把以下内容默认写很长：

- 全部新闻原文
- 大段宏观背景
- 冗长公司介绍
- 所有 supporting events 的逐条展开

## 9. 附录

正式日报默认不带长附录。以下信息转入 sidecar：

- `radar_report_quality_latest`
- `radar_daily_battlecard_quality_latest`
- `radar_price_freshness_latest`
- `radar_ipo_watchlist_latest`
- `radar_research_handoff_latest`
- `radar_daily_battlecard_handoff_latest`

正式邮件附件应读取 `radar_daily_battlecard_latest.md/.pdf`，这是完整日报链通过质量门禁后发布的稳定别名；盘中扫描预览读取 `radar_intraday_scan_report_latest.md`。

## 10. 邮件投递模板

日报邮件的正式 deliverable 应该是：

- 邮件正文：摘要
- 邮件附件：PDF 正稿

不建议：

- 在邮件正文里直接塞整份 Markdown
- 把 Markdown 正稿当成正式投递附件

## 10. 模板示意

```md
---
codex_output: true
codex_output_category: "radar_daily_report"
codex_output_entity: "radar_workspace"
codex_output_title: "Radar 投资机会日报 2026-04-11"
---

# Radar 投资机会日报

截至 2026-04-11 | 运行批次 scan:2026-04-11T09:20:00Z | 市场时区 Asia/Shanghai

## 一、顶部摘要

- 本轮共生成 `18` 个 Radar 候选，其中 `strong_alert 2` 个，`strong_candidate 4` 个，`research_candidate 7` 个，`observe 5` 个。
- 当日实际触发 Bark `2` 条。

## 二、分桶面板

### strong_alert

| 对象 | 类型 | 排名 | 分数 | Why Now | 告警级别 | 下一步 |
| --- | --- | --- | --- | --- | --- | --- |

### strong_candidate

| 对象 | 类型 | 排名 | 分数 | Why Now | 告警级别 | 下一步 |
| --- | --- | --- | --- | --- | --- | --- |

### research_candidate

| 对象 | 类型 | 排名 | 分数 | Why Now | 告警级别 | 下一步 |
| --- | --- | --- | --- | --- | --- | --- |

### observe

| 对象 | 类型 | 排名 | 分数 | Why Now | 告警级别 | 下一步 |
| --- | --- | --- | --- | --- | --- | --- |

## 三、今日 Top Opportunities

### 1. 对象 A | industry | strong_alert

- Why now：
- 核心证据：
- 研究抓手：
- 风险提示：
- 关联标的：

## 四、新增 / 升级 / 观察池

## 五、Bark 事件回顾

## 六、附录
```

## 11. 当前一句话判断

Radar 日报的目标不是“写得全面”，而是“帮你更快决定今天先研究什么”。
