# 外部对标扫描 2026-04-02

## 1. 这轮扫描要回答什么

这轮扫描不只是为了找“有没有人做过股票提醒”。

它要回答的是：

- 有没有接近我们这种 `全行业 + 多重共振 + 自动告警` 的项目
- GitHub、量化论坛、股票论坛里分别有哪些可借鉴路径
- 哪些东西只能借架构，哪些东西能借信号思路

## 2. 总体结论

### 结论 1：完全同构的开源项目很少

到目前为止，我没有看到一个真正与我们目标高度同构的开源项目：

- 面向 `全行业`
- 同时整合 `新闻 / 资金 / 基本面代理 / 政策预期`
- 做 `多重共振识别`
- 部署在服务器上
- 自动推送行业级告警

更常见的是 3 类：

1. `监控/告警框架`
   - 会抓数据、会发提醒，但信号比较简单
2. `单节点研究`
   - 比如只做 news sentiment、只做 sector rotation、只做资金流
3. `人工叙事型帖子`
   - 会讲“政策+资金+景气共振”，但不是自动系统

### 结论 2：外部路径能借很多“零件”，但不能直接照抄成完整系统

目前最现实的借鉴方式是：

- 从 GitHub 借 `监控 / 定时 / 推送 / 状态层`
- 从 QuantConnect / BigQuant 借 `行业级信号构造思路`
- 从股票社区借 `市场上常见的“共振叙事模板”`

### 结论 3：社区经验普遍提醒“噪音”和“假信号”是核心问题

几乎所有相关路径最后都会撞上同一类问题：

- 新闻热度很高，但没有持续行情
- 资金流有异动，但只是短期博弈
- 技术突破和舆情升温出现了，但基本面没有承接

所以外部经验反而支持我们当前的方向：

- 不能做单节点告警
- 必须做多重共振
- 必须保留可复盘记录

## 3. GitHub 类项目

### A. 更像“监控/告警引擎”的项目

#### 1. StockAlert.pro

链接：

- [GitHub](https://github.com/stockalert-pro)

可借的点：

- 这类项目在 `告警产品化` 上比较成熟
- 支持多种 alert type、多通道通知、REST API、webhook
- 对我们最有借鉴意义的不是信号逻辑，而是：
  - 告警对象结构
  - 外部调用接口
  - 多通道通知设计

不该照抄的点：

- 它本质上更像通用 stock alert 平台
- 不等于行业级多重共振系统

#### 2. market-data-notification

链接：

- [GitHub](https://github.com/hanchiang/market-data-notification)

可借的点：

- `定时任务 + 数据抓取 + Telegram 推送` 的最小闭环
- 适合借 `消息发送层` 和 `任务编排思路`

不该照抄的点：

- 信号维度偏简单
- 更像行情通知而不是行业共振识别

#### 3. TradeSignal

链接：

- [GitHub](https://github.com/skadri1601/TradeSignal)

可借的点：

- 有比较完整的 `数据管线 + 后台任务 + 多通道提醒 + 前端展示` 思路
- 适合借：
  - 后台任务结构
  - 事件日志思路
  - 推送和 WebSocket 层的组织方式

不该照抄的点：

- 它盯的是 insider / political trades
- 对象不是行业起势

### B. 更像“市场监控工具箱”的项目

#### 4. xxjwxc/shares

链接：

- [GitHub](https://github.com/xxjwxc/shares)

可借的点：

- A 股语境下的 `分时任务 / 盯盘助手 / 每日监控 / 微信提醒`
- 对我们有参考价值的是：
  - A 股监控系统的模块化形态
  - 任务调度和提醒的产品化思路

不该照抄的点：

- 这是大而全的 A 股量化系统
- 我们不是要做泛化交易终端

#### 5. hxLau/wind

链接：

- [GitHub](https://github.com/hxLau/wind)

可借的点：

- 很典型的 `条件监控 -> 告警` 思路
- 比如放量监控、ABH 相对强弱、基差异常，这些都说明：
  - 行业/资产监控系统可以从简单规则开始
  - 告警去重和阈值管理很重要

不该照抄的点：

- 信号逻辑太单点
- 依赖 Wind 终端环境

### C. 更像“新闻/情绪监控器”的项目

#### 6. tickerpulse-ai

链接：

- [GitHub](https://github.com/amitpatole/tickerpulse-ai)

可借的点：

- 它最值得借的是 `24x7 监控 + 多源采集 + 多 Agent 分工` 思路
- 公开说明里有：
  - 多源新闻和社媒抓取
  - 定时 Scanner / Regime / Investigator 等角色
  - 供应商优先级和 fallback 机制

不该照抄的点：

- 还是偏股票/新闻监控，不是行业多重共振系统
- 新闻维度重，行业聚合不够强

#### 7. World Monitor

链接：

- [GitHub: koala73/worldmonitor](https://github.com/koala73/worldmonitor)
- [World Monitor 官网](https://worldmonitor.app/)
- [Finance Monitor](https://finance.worldmonitor.app/)

可借的点：

- 这是一个非常接近“多源监控产品骨架”的项目
- 它公开展示了：
  - `435+` curated feeds
  - 多变体架构：`world / tech / finance / commodity / happy`
  - `cross-stream correlation`
  - `finance radar`
  - 多层缓存和 self-hosting 能力
- 对我们最有价值的启发不是它的具体对象，而是：
  - 异质源如何统一接入
  - 监控系统如何做成“可读的 intelligence 产品”
  - 多流信号如何被组织成统一的事件与面板

不该照抄的点：

- 它的核心对象是 `国家 / 地缘 / 世界事件`
- 我们的核心对象是 `行业 / 主题 / 产业链`
- 它的很多数据流是：
  - 军事
  - 航空
  - 灾害
  - 网络安全
  - 这些与我们的投资监控对象不同
- 它更偏 `situational awareness dashboard`
- 我们更偏 `行业级事件压缩 + 告警治理 + 投资可用性验证`
- 它的 license 为 `AGPL-3.0`，而 README 同时写了商业使用需要额外许可
  - 如果未来真要复用代码，需要单独看 license 边界

当前判断：

- 如果说 StockAlert.pro 更像“告警引擎”
- tickerpulse-ai 更像“新闻监控器”
- 那么 World Monitor 更像“多源态势监控产品骨架”

这类项目对我们最大的意义是：

- 它证明了“大量异质源 + 摘要 + 相关性 + 面板 + 变体”这类系统形态是成立的
- 所以我们不需要怀疑“做成一个长期监控系统”这件事本身
- 我们要解决的是如何把这套形态改造成 `行业起势捕捉系统`

## 4. 量化论坛 / 研究社区

### A. QuantConnect

#### 1. Sector Rotation Based on News Sentiment

链接：

- [QuantConnect](https://www.quantconnect.com/research/15309/sector-rotation-based-on-news-sentiment/)

可借的点：

- 明确展示了 `先对成分股新闻做聚合，再上升到 sector ETF` 的思路
- 这和我们后面做行业级新闻聚合非常接近

我们该借的不是：

- 它的具体交易组合

我们该借的是：

- 行业级新闻聚合方法
- 行业对象和新闻对象之间的映射方式

#### 2. Factor Sector Rotation with Kavout

链接：

- [QuantConnect](https://www.quantconnect.com/research/17896/factor-sector-rotation-with-kavout/)

可借的点：

- 说明 `先个股、后行业` 的因子归集路径是可行的
- 行业层的信号可以来自底层股票的聚合

### B. BigQuant

这轮扫描里，BigQuant 上最接近我们的，不是具体代码，而是方法组合。

#### 1. 资金 + 景气改善

链接：

- [行业轮动策略：关注资金与景气改善，布局消费及采掘](https://bigquant.com/square/paper/5e891bae-785b-4e8e-bc95-c0f88c70f232)

可借的点：

- 资金流和景气指标可以并用
- 宏观、行业、资金三个层次可以串成一个框架

#### 2. 景气投资顶层设计

链接：

- [行业景气投资的顶层设计和落地方案](https://bigquant.com/square/paper/93a6daa8-aa79-412e-8bd9-917e1d9192ba)

可借的点：

- 高频产业链指标数据库
- Nowcasting 思路
- 行业净利润和营收变化的代理构造方式

#### 3. 资金流共振

链接：

- [视角透析：基于资金流共振的行业交易策略](https://bigquant.com/square/paper/5a2e6fef-45bc-41cd-9d02-107980c2542f)

可借的点：

- “共振”不是我们自己发明的说法，行业资金共振已经被系统讨论过
- 但外部结果也提示：不同资金之间的共振并不普遍，很多行业相关度其实不高

这对我们很重要，因为它说明：

- 共振必须做验证
- 不能假设所有节点天然同步

#### 4. 分歧与共振

链接：

- [全面发力关键期，看好科技产业长期趋势](https://mf.bigquant.com/square/paper/f21eae3d-b9d2-4f1d-859d-1bc67329ad81)

可借的点：

- “分歧与共振”已经是成熟的行业轮动研究语言
- 说明我们项目的表达方式是贴近实务的

### C. JoinQuant / 聚宽

#### 1. 行业概念数据接口

链接：

- [JoinQuant 行业概念数据](https://www.joinquant.com/help/api/plateData)

可借的点：

- 行业骨架和概念骨架可以先借现成板块数据体系
- 对我们目前最直接的价值是：
  - 全行业覆盖骨架
  - 行业 / 概念 / 主题的基础映射

## 5. 股票 / 投资讨论社区

### A. 雪球

雪球上没有看到成熟的自动系统，但看到很多 `共振叙事模板`。

这类帖子常见结构是：

- 政策催化
- 资金流入
- 基本面改善
- 技术突破
- 代表股领涨

例如：

- [化学制药板块深度解析：政策、创新与全球化驱动的结构性机遇](https://xueqiu.com/9743557912/337324769)
- [券商板块异动简析：政策、业绩与并购共振下的爆发？](https://xueqiu.com/5044069008/338558889)
- [电网设备：2026年确定性最高的主线赛道](https://xueqiu.com/1474936650/377127395)

可借的点：

- 市场最终是怎么讲“共振”的
- 哪些叙事元素最容易被投资者当成起势信号

不该照抄的点：

- 这些大多是事后解释或半事后总结
- 不能直接拿来当信号定义

### B. Reddit / algotrading

这类社区更有价值的不是现成项目，而是失败经验和现实提醒。

例如：

- [Which AI Agent to use for sentiment and numerical tracking](https://www.reddit.com/r/algotrading/comments/1njpp7i)
- [Sentiment Based Trading strategy - stupid idea?](https://www.reddit.com/r/algotrading/comments/1jvftsj)

可借的点：

- 很多人都在想做“新闻/情绪/变动 -> 自动提醒”
- 但社区对噪音、延迟、误判的担忧非常强

这和我们的成功标准是吻合的：

- 不能只做一个会抓新闻的系统
- 必须证明它有投资增量

## 6. 这轮扫描后最值得借的东西

### 最值得借的 4 类东西

#### 1. `告警基础设施`

来源：

- StockAlert.pro
- market-data-notification
- TradeSignal
- World Monitor

借法：

- 消息结构
- 多通道通知
- 后台任务和状态日志

#### 2. `行业级聚合思路`

来源：

- QuantConnect 的 sector sentiment 路径
- World Monitor 的 cross-stream correlation 表达方式

借法：

- 先做底层个股/新闻聚合，再抬到行业层

#### 3. `景气 + 资金 + 宏观组合框架`

来源：

- BigQuant 多篇行业轮动 / 景气投资研究

借法：

- 行业信号不做单因子
- 节点间允许“分歧”，但重点观察“共振”

#### 4. `叙事模板`

来源：

- 雪球和股票社区

借法：

- 知道市场最后会用什么语言描述行业起势
- 反过来帮助我们设计可解释告警文案

#### 5. `多源监控产品形态`

来源：

- World Monitor

借法：

- source manifest 的组织方法
- 多变体监控界面
- 异质源统一为分析产品的方式
- 多流相关性如何被表达成“值得看”的信号面板

## 7. 这轮扫描后不应该借的东西

- 不照搬任何一篇行业研究的对象边界
- 不照搬任何单一论坛帖子的“主线判断”
- 不把单一 news sentiment 或单一资金流模型误当成我们的系统主体
- 不把“会发提醒”误当成“系统成功”

## 8. 当前一句话结论

如果把这轮扫描压成一句话，我的结论是：

`外部世界已经把“数据采集”“行业轮动”“资金流”“景气投资”“新闻情绪”“告警推送”这些零件分别做过很多次，但几乎没人把它们真正拼成一个面向全行业、多重共振、自动推送的行业起势捕捉系统。`

这意味着：

- 我们不是在空想
- 但我们也不能照抄现成项目
- 最现实的路线是：借零件，自己组装
