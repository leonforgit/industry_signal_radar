# Radar PM 级日报规则 V1

这份文档不是解释某一天的报告写得怎么样，而是固定 `Radar 如何持续生成 PM 级事件驱动日报` 的方法模板。

目标不是把单日内容修顺，而是让系统以后每天都按同一套标准产出。

## 1. 目标定义

一份合格的 Radar 日报，应该是：

- 一个 `事件驱动研究前置板`
- 一个 `研究优先级分流器`
- 一个 `催化剂与确认缺口的压缩器`
- 一个 `晨会前一到两页 brief`

它不应该退化成：

- 新闻摘要
- 宽口径业绩快报榜
- 单纯行业热度榜
- 全量系统状态转储

## 2. 生成规则

### 2.1 样本规则

- 正文顶部只保留报告日期和今日主线，不再展示 `本地生成 / 运行批次 / 市场时区 / sample_age_days` 等工程字段。
- 工程字段应进入隐藏 footer 或 quality sidecar；PM 只在后台状态里看到是否影响可信度的结论。
- 不允许把旧样本伪装成当天 live 报告；`freshness` 的主判断应基于 `expected_sample_date / freshness_lag_days`。
- 当样本偏旧或价格补数存在前排缺口时，必须进入后台状态或 quality blocker，不能只留在日志里。

### 2.2 对象规则

- 主榜默认允许 `company / industry / macro` 混排。
- `Top Opportunities` 不允许包含 `background_only` 对象。
- `Immediate Research Queue` 只应该保留少量高优先级对象，默认目标是：
  - 至少 `1`
  - 最多 `12`
- 正式日报的 `Top Opportunities` 默认只展开 `2-3` 个对象；其余 immediate 对象留在 handoff。
- 公司对象进入主报告前台时，必须优先使用目标交易日价格；仍缺最新价格的对象只在 warning / handoff 中保留，不默认占用 Top Opportunities。
- 纯财报触发的公司对象进入 Top Opportunities 前必须做价格预检：
  - 若事件发生在最新价格样本之后，只能作为首个交易日确认项，自动降权。
  - 若最新收盘涨幅、量比或近 5 日阳线占比显示利好已被明显交易，只能作为承接验证项，自动降权。
  - 若只是宽口径业绩快报、没有订单/并购/获批/产能等结构性二级证据，不应占据高权重机会位。

### 2.3 分流规则

- `immediate_research`
  - 必须是今天值得优先打开的对象
  - 不能退化成宽口径业绩名单
- `thesis_watch`
  - 可以保留早期线索和强候选
  - 但必须明确下一里程碑与确认缺口
- `risk_review`
  - 只服务防守和复核
- `background_only`
  - 不进入 Top Opportunities

### 2.4 行业卡片规则

- 行业卡片优先展示：
  - `announcement`
  - `proxy`
  - 高分具体公司事件
- 行业卡片必须展示 `representative stock / ETF proxy`，并明确这些标的是跟踪变量，不是交易建议。
- 如果没有足够高质量事件，必须退回：
  - `总分 / 行业状态`
- 不允许为了“看起来有内容”硬挂低质量 headline。

### 2.5 Kimi / IPO 规则

- Kimi 层不是第二份长报告，而是 PM 编辑台的初步研究层。
- Kimi 必须分成两层：`editorial layer` 负责压缩表达，`research harness` 负责渲染前预检和排序约束。
- `research harness` 可用时，Top/递补候选必须先得到结构化 verdict；`watch_only / reject / risk_review / research_gap` 不允许进入 Top Opportunities。
- Top Opportunities 若缺少 Kimi verdict，应视为 quality blocker，而不是只留 warning。
- Kimi 可用时，输出应包含：
  - `preliminary_research`：机会雏形、初判、今天最该补的验证
  - `ipo_watchlist`：对今日 A/H 新股的申购价值初判
- Kimi 不可用时，日报必须自动降级，不能阻塞主链。
- IPO watchlist 只允许基于结构化新股日历和招股/发行字段，不允许编造今日新股。
- A/H IPO 都要覆盖；港股侧如果 HKEX 日历只给英文名称，系统应尽可能映射中文名或中文简称。
- IPO 初筛的核心输出是“申购 / 小额申购 / 暂缓申购 / 放弃 / 已过申购窗口”的研究 stance；不输出二级市场买入、卖出或上市首日追踪动作。
- IPO 不是只列名单：申购前必须尽量完成招股书业务/财务/风险摘要、申购拥挤度、可比公司估值、保荐人和基石投资者预检。
- IPO 估值必须区分“发行人自身估值”和“可比公司估值”：能拿到发行市值和最近一年利润/亏损时，必须给发行 PE；亏损公司必须标明 PE 不适用/机械负 PE，并优先补 PS 或市值/收入口径。
- 不达标、暂缓或已过窗口的新股也要列出公司名和跳过理由，进入“不达标 / 跳过”组，方便复盘打新模型，而不是静默丢弃。
- IPO 段落默认用表格化研究卡片；发行市值统一转换为万/亿单位，可比公司和保荐人有中文名时优先使用中文名。

### 2.6 文本规则

- 报告正文不应出现原始源标签，例如 `akshare:*`
- 不应出现工程占位符，例如：
  - `股票代码：待补映射`
  - `股票代码：未确认`
- Top Opportunities 需要显式展示：
  - `事件/证据`
  - `最新市场反应`
  - `今日动作`
  - `主要缺口`
- 今日关键事件不能只复述新闻标题，必须给出：
  - `看多 / 看空方向`
  - `可观察标的`
  - `逻辑传导`
  - `情景弹性区间`
  - `当天验证动作`
- 事件弹性只能写成情景假设，不得写成确定收益预测。
- 电子行业事件必须区分子链：半导体设备、后道封测设备、消费电子、光模块/高速连接不能互相泛化。单一公司订单只能先传导到直接公司和同类设备，再用更多同链条订单/交付确认是否扩散到 ETF。
- 如果 snapshot 带有 `market_sentiment_context`，顶部摘要必须显式翻译成 PM 可读语言：
  - `资金流情绪`
  - `市场事件情绪`
  - `综合情绪`
- 如果公司对象带有 `sentiment_context`，Top Opportunities 应展示：
  - `公司事件情绪`
  - `行情情绪`
  - `综合情绪`
  - 但情绪不能替代 `why_now / evidence / confirmation gap`

### 2.7 邮件投递规则

- 每日自动邮件应以 `PDF` 作为正式日报附件。
- 邮件正文只保留：
  - 运行批次
  - quality gate
  - 研究分流摘要
  - Top Opportunities
  - 市场情绪摘要
- 不再把整份 Markdown 日报正文直接塞进邮件正文。

## 3. 排序规则

日报展示优先级默认按下面顺序走：

1. `triage_action`
2. `evidence_quality`
3. `radar_bucket`
4. `radar_score`

也就是说，`更应该立刻研究` 的对象，优先级要高于 `分数略高但只是 thesis_watch` 的对象。

## 4. 质量检查

这套规则必须被机器检查，而不是只靠人工阅读。

当前对应的规则配置与检查入口是：

- 配置：[radar_report_rules_v1.json](config/radar_report_rules_v1.json)
- 检查脚本：[check_radar_report_quality.py](scripts/check_radar_report_quality.py)
- 主动 freshness 编排：[ensure_radar_freshness.py](scripts/ensure_radar_freshness.py)

每天生成日报后，质量检查至少要回答：

- 是否缺少关键章节
- 当前市场样本是否落后于 `expected_sample_date`
- `Immediate Research Queue` 是否过宽或过空
- `Top Opportunities` 是否避开 `background_only` 和缺少最新价格确认的公司对象
- `Catalyst Inventory` 是否和 snapshot 对齐
- 报告里是否残留源标签或工程占位符
- IPO watchlist 是否成功生成、是否同时覆盖可申购与不达标对象、是否具备招股书/拥挤度/可比估值/保荐人字段
- 当天是否存在需要人工复核的质量 warning
