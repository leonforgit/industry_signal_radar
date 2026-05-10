# 新闻源研究笔记

## 目标

这轮研究的目标不是继续加源，而是回答：

- 什么样的新闻源值得进入行业雷达
- 什么样的新闻源只会放大噪音
- 新闻层应该怎样分层

## 核心结论

- GitHub 上的公开项目，普遍走的是 `多源聚合`，不是单一新闻源
- 量化论坛普遍把新闻 / 情绪视作 `快变量`，不能单独使用
- 股票论坛适合观察 `题材扩散`，不适合做高置信主触发
- Reddit 经验更强调 `主题提取 / 去重 / 聚类`，而不是只做 headline sentiment

## 这轮最重要的判断

新闻源接入应该先回答 4 个问题：

1. 它到底是 `硬信息源`、`专业快讯源`、`泛财经源`，还是 `社区源`
2. 它能不能稳定映射到行业
3. 它和现有源相比，新增信息量有多大
4. 它会不会明显放大误匹配和重复转载

## 当前建议

- 暂停“看到一个能抓的就接一个”的模式
- 先以 `docs/news_source_admission_policy.md` 为准
- 后续任何新闻源接入，都先过准入规则再进入主链

## Kimi 审阅补充

这轮我也让 Kimi 做了一次只读审阅，它补的几个点是对的：

- 新源不能只看“能不能抓”，还要量化和现有主源的重叠
- 时间戳不只是有字段，还要有统一口径和新鲜度判断
- 去重要能区分转载、近重复和同一事件更新
- 社区源要防刷量、搬运和叙事操纵
- 新闻源接入后还要持续观察质量衰减

这些意见已经补进 `docs/news_source_admission_policy.md`，所以现在这份规则比第一版更适合进入执行层。

## 第一版重叠快照

这轮我没有继续补新源，而是先对当前三源做了第一版 overlap 快照：

- `news_cctv`：`13` 条可用标题，`13` 条唯一标题，`date_cov = 1.00`，`time_cov = 0.00`
- `stock_info_global_cls`：`16` 条可用标题，`16` 条唯一标题，`date_cov = 1.00`，`time_cov = 1.00`
- `stock_info_global_em`：`200` 条可用标题，`200` 条唯一标题，`date_cov = 1.00`，`time_cov = 1.00`
- `CLS <-> EM`：`exact_overlap = 5`，`approx_overlap = 6`
- `CCTV <-> CLS/EM`：当前快照 `exact_overlap = 0`，`approx_overlap = 0`

这个结果说明：

- `CCTV` 目前更像低重叠的政策 / 宏观层
- `CLS` 和 `EM` 已经开始出现同层快讯重叠

所以现阶段最优先的动作，不是继续找更多泛财经源，而是：

- 先补 `时间戳归一`
- 先补 `跨源去重 / 事件簇`
- 再做行业映射命中率验证

对应快照见：

- `workpapers/news_source_overlap/news_source_overlap_20260403T132119Z.md`

## 第一版行业映射命中率快照

我又补了一层行业映射命中率快照，结果也很有用：

- `news_cctv`：`single_hit = 5 / multi_hit = 2 / unmatched = 6`
- `stock_info_global_cls`：`single_hit = 6 / multi_hit = 0 / unmatched = 14`
- `stock_info_global_em`：`single_hit = 64 / multi_hit = 11 / unmatched = 125`

这一层说明当前关键词映射的主要短板，不是“歧义特别高”，而是：

- 还有大量新闻根本没有命中任何行业

所以新闻层下一步不能只补 `去重 / 聚类`，还要同步补：

- 行业别名
- 产业链词
- `heat_keywords` 扩表

对应快照见：

- `workpapers/news_source_mapping/news_source_mapping_20260403T133643Z.md`

## Kimi 扩表回合

这轮我没有把关键词扩表完全手工做，而是把“机械扩词”交给了 Kimi，我来做 review 和验收。

最终保留的方向包括：

- `农林牧渔 -> 春耕`
- `商贸零售 -> 消费品`
- `电力设备 -> 储能 / 电芯`
- `机械设备 -> 机器人`
- `国防军工 -> 导弹 / 军队 / 武装 / 空袭`
- `传媒 -> 电影 / 票房`
- `石油石化 -> 油田`
- `社会服务 -> 文旅`

我人工拒绝了：

- `建筑装饰 -> 船舶 / 造船`

原因很简单：这类词虽然可能帮某些标题命中，但错配风险太高，不适合直接收进主口径。

最新命中率快照相对基线的改善是：

- `news_cctv`：`single_hit 5 -> 7`，`unmatched 6 -> 4`
- `stock_info_global_cls`：`single_hit 6 -> 7`，`multi_hit 0 -> 1`，`unmatched 14 -> 12`
- `stock_info_global_em`：`single_hit 64 -> 81`，`multi_hit 11 -> 10`，`unmatched 125 -> 109`

这一轮给我的判断是：

- 继续盲目补新闻源，优先级不如继续补关键词和行业别名
- 让 Kimi 先做机械扩表，再由主控代理做 review，这条协作路径是有效的
