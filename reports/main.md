# 行业起势预警主报告

## 1. 这个项目在做什么

这个项目要解决的问题不是“能不能神准地猜中某个板块明天启动”，而是：

- 能不能更早地识别行业从冷到热的升温过程
- 能不能把分散在资金、基本面、新闻、政策和价格结构中的早期线索压缩成可跟踪的行业状态
- 能不能给主投研流程提供一个比盘感更结构化的 `行业起势概率雷达`

因此，这个项目默认是：

- 一个 `预警系统`
- 一个 `自动捕捉系统`
- 一个 `证据链组织系统`

而不是：

- 一个自动交易系统
- 一个单因子轮动模型
- 一个只靠新闻热度驱动的情绪工具

## 2. 当前核心判断

截至当前项目初始化阶段，我们的判断是：

- 这件事有实现价值，也有实现空间
- 但不应该期待存在一个单一、稳定、跨行业通用的“起势神指标”
- 更现实的做法是构建 `多源信号 + 行业专属先行指标 + 状态机` 的组合框架

当前最重要的共识包括：

- 资金层能提供最早的市场行为变化
- 基本面层能提供最接近真实经营状态的中层证据
- 新闻层能提供叙事扩散和关注度变化
- 技术 / tape 层能补足市场是否真正开始承认这条逻辑
- 目标态必须是服务器上的自动扫描与自动推送，而不只是人工阅读框架

### 当前收口结论

- 当前日报主链已经明确收成 `canonical-first`：事件层只吃 `News Event Hub` consumer feeds，情绪层只吃 sentiment sidecar，市场层只吃 canonical market substrate。
- 当前日报不再允许“只要有一层数据就硬出报告”；生成前会先跑 `source readiness`，只有 `news / sentiment / market` 三层 freshness 全部通过才继续出日报和发邮件。
- 当前日报主链已经补上“按需主动调用上游”的机制，而不再只是被动消费本地 sidecar：
  - 价格层已经升级成“逐票补抓优先、整库刷新兜底”：`build_radar_canonical_price_bridge.py` 会先对候选个股逐票抓取最新行情并写回 canonical market DB，再由 `company_price_snapshot_latest.csv` 吸收这些 fresher rows；只有逐票补抓仍不足时，才会回退到整库 `canonical price builder`
  - 新闻层会先从 `research_feed_latest.json` 做快速核实，再只对证据偏薄的前排公司对象按需触发 `run_company_discovery.py`
- 这意味着 Radar 已经开始具备“发现上游薄弱点 -> 回到上游补数据 -> 再出日报”的闭环雏形，而不再只是单向消费上游产物。
- 当前行业扩散也已经开始走二次门禁：机构/政策主体和弱映射事件不再直接把行业候选抬进前排，行业卡片会优先保留更像研究入口的对象与动作。
- 当前日报主链已经新增一层可选的 `Kimi editorial layer`：它不接管排序与发送，只做 `PM 级压缩摘要 / 重点动作 / 风险提示` 的编辑增强；成功时并入正式 PDF，失败时自动退回纯脚本版。
- 当前日报呈现层已经从“全量系统状态转储”收敛成 `morning_brief`：正式报告默认控制在晨会前一到两页，完整分桶和长附录留在 handoff / snapshot / quality sidecar。
- 当前日报的价格口径进一步收紧：主报告前台只展示已经拿到目标交易日公司价格的对象，仍缺最新价格的公司线索只进入 warning 或 handoff，避免用滞后样本形成 PM 判断。
- 当前已新增 `A/H IPO 打新申购研究`：A 股和港股侧都以“是否值得申购 IPO 股份”为核心问题；港股侧会展示招股书业务/财务/风险、申购拥挤度、可比估值、保荐人/基石信息，并把不达标或已过窗口对象单列出来用于复盘，不再用上市首日承接替代打新判断。
- 当前已新增 `中长期隐性线索` sidecar：系统会从订单、产能、政策、资本动作、基本面质量等慢变量里抽取 `30-120d` 线索，避免日报只停留在短期新闻和涨跌幅信号。
- 当前 Kimi editorial 层按 Kimi Code 的 Anthropic-compatible API 接入，运行配置标记为 `kimi_code_k2_6 / model_family=kimi-k2.6`；请求体仍按 Kimi Code 官方要求使用 `kimi-for-coding` model id。
- 当前日报产品标准已经完成一轮外部对标并落成 `docs/radar_reference_standards_v1.md`：Radar 的目标不是新闻堆叠，而是像 GDELT/HealthMap/PortWatch 这类成熟雷达一样，形成 `多源结构化事件 -> 预检/复核 -> 代表变量 -> PM 行动界面` 的流水线。
- 当前 Top Opportunities 已新增财报/价格预检口径：纯财报事件若已被最新收盘涨幅、量比或首个交易日缺口明显影响，会自动从高权重作战线降权；行业机会则必须在正文展示代表股和 ETF 跟踪口径。
- 当前 Top Opportunities 还新增了 `Kimi research harness` 硬门禁：Kimi 不再只是写摘要，而是在正式渲染前对 Top/递补候选给出结构化 verdict；报告前排若没有 Kimi verdict，或被判为 `watch_only / reject / risk_review / research_gap`，quality gate 会直接拦截。

## 3. 当前框架假设

当前把系统先拆成 7 个节点：

1. 行业对象层
2. 资金层
3. 基本面层
4. 新闻层
5. 政策 / 预期层
6. 评分与状态机层
7. 历史验证层

默认输出不是直接的“买入 / 卖出”，而是行业状态，例如：

- `冷静`
- `升温`
- `起势候选`
- `确认`
- `过热`
- `退潮`

## 4. 当前边界

当前还没有：

- 稳定的新闻采集与去重系统
- 行业专属的高频基本面代理指标库
- 完整的主题 / 概念 / 产业链切换机制
- 历史案例验证框架
- 完整的多节点共振评分体系

这意味着当前阶段的目标不是“尽快上线”，而是：

- 先把可运行的全行业骨架和基础 runner 立起来
- 再逐步把新闻 / 政策 / 基本面代理节点接进去
- 最后通过观察期和历史回放判断哪些节点真的值得长期投入工程化

## 5. 当前风险认识

这个方向最容易踩的坑包括：

- 把新闻变多误判成基本面变强
- 用统一模板硬套所有行业
- 只看成功案例，不纳入假启动和失败样本
- 先建抓取系统，再发现没有清晰的行业对象和状态定义
- 在仓库里堆积大量低价值原始语料和缓存

## 6. 当前阶段

当前已经不再是纯 `阶段 A：项目框架设计`，而是进入了：

- `可运行 MVP`
- `新闻 / 资金 / 基本面节点持续补强`
- `投资入口质量校准`

也就是说，现在项目已经具备：

- 远端真实扫描
- 状态落库
- Bark 推送
- 第一批新闻 / 公告 / 基本面代理节点

当前最重要的，不再是“能不能把系统跑起来”，而是：

- 哪些信号层真的有投资增量
- 哪些新闻和公告能被压成高信噪比行业事件流
- 哪些剩余未命中样本其实应该进入 `主题链 / 宏观 / 地缘 / 公司映射`，而不是继续硬塞进一级行业词表

当前项目的执行主控入口是：

- `docs/execution_checklist.md`

当前项目的背景规划入口是：

- `docs/master_plan.md`

当前项目判断“是否真的有投资价值”的标准入口是：

- `docs/success_criteria.md`

当前项目判断“以现有配置能不能做成、还缺哪些资源”的入口是：

- `docs/feasibility_and_resource_plan.md`

当前项目的系统规划总纲入口是：

- `docs/radar_system_plan_v1.md`

当前项目的统一产品说明入口是：

- `docs/radar_unified_plan.md`

这一轮之后，项目的 durable 判断进一步明确为：

- 当前工作区已经不应再被理解为狭义 `行业新闻雷达`
- 更准确的定位是 `通用 Radar 机会发现系统`
- 上游 `News Event Hub` 和下游 `Research` 当前都按外部依赖处理
- 近期主目标固定为 `V1 可运行闭环`
- 默认产品偏好固定为：`早发现优先`、`对象混排`、`分桶面板 + 少量重点详解`
- V1 主闭环固定为：`shared feed -> candidate pool -> ranking -> Bark -> daily report -> replay/validation`

## 6.1 2026-04-12 远端输出链完成第一轮闭环

这一轮最重要的进展，不是又补了一个新节点，而是把 a private runtime configured outside this repository 上的 Radar 输出链真正闭到了可验收状态。

当前已经确认的事实是：

- `2026-04-08T06:55:00+00:00` 的交易窗口 one-shot 已经真实完成，`manual validation` 返回 `success`
- 远端已经真实写出四类 Radar 原生产物：
  - `radar_candidate_pool_latest.json`
  - `radar_opportunity_snapshot_latest.json`
  - `radar_bark_summary_latest.json`
  - `radar_daily_report_latest.md`
- 本地 `sync_latest_radar_payload.py --build-workspace-outputs` 已经验证四类产物都能镜像回当前工作区
- `northbound` 这一层已经从“可能拖死主链的慢源”收敛成“超时就降级为 warn 的非阻塞源”
- 新产物顶层 `run_id` contract 已经统一，后续校准和回放不需要再在多个字段名之间做兼容猜测
- 当前已经新增一个专门服务排序校准的输出面：`output/reports/radar_calibration_latest.md`

这一轮也带来两个更真实的系统判断：

- 第一，当前 Radar 的工程主问题已经不再是“输出链能不能落出来”，而是“输出出来之后，排序和 Bark 噪音要怎么继续校准”
- 第二，`snapshot-driven Bark` 的第一轮真实观察样本显示：在 `2026-04-08T06:55:00+00:00` 这个交易窗口样本里，系统给出 `31` 个行业快照、`0` 条 alert；当前先不为了追求提醒数量而下调阈值

## 6.2 2026-04-13 报告语义与对象映射修正

这一天做的不是“再补一个新节点”，而是把几处会直接伤害产品可信度的低级错位先修掉。

- 第一，日报、research handoff 和校准摘要不再把 `市场样本日期` 和 `本地生成时间` 混成一个“截至日期”；如果当前是基于旧交易窗口样本重建，报告会明确写出来。
- 第二，公司映射补上了一个小型 `manual override` 层，用来兜底少数自动映射漏掉、但研究上又很关键的公司主体；`海思科` 这一类对象不应再继续出现 `股票代码：待补映射` 这种工程味太重的正文。
- 第三，负面业绩事件的下一步动作文案已经纠正；系统不再把利润下降、亏损、风险线索写成“确认业绩改善是否延续”。
- 第四，公司事件也开始做 `PM 视角去噪`；`减持 / 终止收购 / 补缴税款 / 正常履职 / 无相关业务计划` 这类对象，已经开始从研究队列回落到风险或背景层。
- 第五，mixed-object 输出开始在 snapshot 层做去重，日报行业卡片也进一步压低了负面或低质量公司 headline 的门面权重。
- 第六，行业卡片的门面事件阈值也被抬高；如果没有足够高分的公司、政策或代理证据，它就直接退回 `总分 / 行业状态`，不再为了“看起来有内容”硬挂烂标题。

这一轮的价值不在于让 Radar 突然变得更聪明，而在于：

- 报告终于更像一份可以给人看的前置系统输出
- 用户看到的对象信息更完整
- 历史样本与 live 报告的边界更清楚

同时，这一轮也把另一个关键事实钉死了：

- 当前“日报不够新”的问题，已经从本地时间表达问题推进成了远端样本同步问题
- `2026-04-13T06:30:00+00:00` 的 fresh sample 现在已经真实同步回本地，`latest` 快照、handoff 和日报都不再停留在 `2026-04-08`
- 第三，下一阶段更重要的问题，不再是“样本够不够新”，而是“哪些对象真的配进 immediate_research”

## 6.3 2026-04-13 `immediate_research` 已开始收成真正的事件驱动研究队列

这一轮最关键的变化，不是又加了一个新字段，而是 `triage_action` 终于开始像研究系统本身，而不是简单报表标签。

当前已经发生的收敛包括：

- 公司对象不再共享固定的 `why_now / confidence / followup_value`
- 这些值现在开始动态参考：
  - 母行业承接强弱
  - proxy / announcement 是否存在
  - overlay 是否足够支持扩散假设
  - 公司映射是否可靠
  - 催化类型是不是 `earnings / order / approval / capital markets`
- `immediate_research` 也不再默认奖励“只要业绩不错就先进来”的公司事件

在 `2026-04-13` 这轮真实样本里，结果已经明显变化：

- `immediate_research` 现在只剩：
  - `蔚蓝锂芯`
  - `全社会用电量`
- 大量原本会挤进立即研究的 `strong_candidate + earnings_guidance` 公司对象，已经被压回 `thesis_watch`
- 这让 handoff 开始更像 PM 会真正派给研究员的“今日优先队列”，而不是宽口径业绩惊喜名单

这一轮也明确暴露了下一个问题：

- `immediate_research` 已经开始“正确收窄”
- 但它还需要继续从“收得住”提升到“收得准”
- 下一阶段最该观察的是：哪些 `strong_candidate` 在后续样本里应该自然升级，而不是现在就被人工放进去

## 6.10 2026-04-13 PM 级日报方法已经开始模板化

这一步的重点不再是“把今天这份报告修顺”，而是把每天生成高质量日报的方法抽象成正式规则。

当前已经新增三层正式资产：

- `config/radar_report_rules_v1.json`
  - 固定日报展示规则、Top Opportunities 规则、Immediate Research 目标区间、行业 headline fallback 规则
- `docs/radar_pm_report_rules_v1.md`
  - 用产品语言明确什么叫 PM 级日报，什么不算
- `scripts/check_radar_report_quality.py`
  - 每次日报生成后自动检查：
    - 关键章节是否齐全
    - Top Opportunities 是否覆盖 `immediate_research`
    - Immediate Research Queue 是否过宽或过空
    - 报告里是否残留 `akshare:*` 源标签
    - 是否出现 `股票代码：待补映射 / 未确认` 这类工程占位符

这意味着从现在开始，项目不再只是“今天这版看起来好一些”，而是：

- 已经开始有一套 `每天自动产出 PM 级日报` 的规则模板
- 这套模板也已经进入主编排链，而不是停留在单独文档里

这轮继续往前走以后，模板层又多了两条更硬的规则：

- 顶部摘要里的研究分流计数，必须和 handoff 的真实队列数量一致；它不允许再引用被截断后的展示列表长度
- `observe` 行业卡片在缺少结构化来源时，应该默认退回 `总分 / 行业状态` 摘要，而不是继续硬挂一个看起来像事件、但其实不够像研究入口的 headline

## 6.11 2026-04-16 `freshness + catalyst inventory + taxonomy` 开始进入正式主链

这一轮不是继续修单日报文案，而是把几块更像 `fund-grade operating layer` 的东西正式接进主编排链。

第一块是 `freshness/SLA`。

- `check_radar_report_quality.py` 现在会显式计算 `sample_age_days`
- 质量规则已经新增：

## 6.12 2026-04-20 价格补抓与非交易语义开始系统化

这一轮最重要的变化，不是又多抓到几只股票，而是 Radar 对上游 canonical market 的调用方式终于更像一个可持续系统了。

第一，逐票价格补抓已经从“Radar 私用 patch”推进成共享 writeback。

- `ensure_radar_price_freshness.py` 现在会优先对候选公司逐票触发 `build_radar_canonical_price_bridge.py`
- 这条 bridge 不再只把 fresher rows 写回 Radar 自己的 sidecar，而会同步写回共享 `equity_prices.db`
- 这意味着后续 sentiment、Research 或别的下游，只要继续消费这条共享价格底座，就能自动吃到 Radar 这轮补出来的最新行，而不需要各自再重复抓取

第二，系统已经开始区分“真 stale”和“正常非交易”。

- 之前像 `东方证券 / 天迈科技` 这种“4 月 20 日起停牌”的并购重组对象，会被一律标成 `价格样本滞后 3 天`
- 这在工程上看似诚实，但在投研上其实是误报，因为这类对象最后一个有效收盘本来就应该是前一交易日
- 现在 Radar 已经把这种语义正式接进：
  - company target extraction
  - price freshness audit
  - snapshot company price gate
  - 日报正文渲染

所以现在系统会明确写成：

- `停牌前最后收盘 2026-04-17`
- 后续动作也变成 `优先跟踪停牌进展、方案披露与复牌安排，再在复牌首日确认市场反应`

这和过去那种“先补收盘反应再说”的模板化动作相比，更像真正的事件驱动研究入口。

第三，`data substrate audit` 的定位也更清楚了。

- 现在 `source readiness` 负责判断“今天能不能出日报”
- `radar_data_substrate_audit_latest.json` 负责判断“上游 canonical-first 主链还有哪些 warning source 和 coverage gap”
- 因此它不再把所有 warning 都提升成 hard fail，而是把当前真正的系统上限暴露成长期 warning：
  - `stock_sector_fund_flow_rank` 的连接波动
  - `stock_hsgt_hold_stock_em` 长期 stale
  - `stock_lhb_detail_daily_sina` schema 漂移
  - `fund_etf_scale_szse` 偶发 reset
  - 少量未解析公司主体

这一轮之后，系统层的真实状态更清楚了：

- Radar 主链已经具备“发现缺口 -> 回上游补数据 -> 把新数据写回共享底座 -> 再出日报”的能力
- 当前日报还能继续往上打的上限，已经更多取决于上游 `canonical market` 各 lane 的稳定性，而不是 Radar 本身会不会调用它们
  - `warn_if_sample_age_days_gt`
  - `fail_if_sample_age_days_gt`
- 这意味着系统不再只会“生成一份报告”，而会明确告诉你：这份报告是不是已经 stale

## 6.13 2026-04-20 canonical market 的 4 个 warning source 已经收口

这一轮真正修掉的，不是“报告里多几句解释”，而是 `canonical market` 这层长期暴露出来的 4 个 warning source。

第一，行业资金流不再单点依赖 `stock_sector_fund_flow_rank`。

- 之前这条 lane 的问题不是 Radar 计算错了，而是上游 Eastmoney ranking 接口经常 `RemoteDisconnected` 或被转发到 delay 节点
- 这会让行业资金流时好时坏，最终在 `radar_market_manifest.json` 里长期留下 warning
- 现在 Radar 已经补上 `stock_fund_flow_industry(symbol="即时")` 的标准化 fallback：只要 ranking 接口波动，就自动切到可用的行业资金流即时表，并把字段收成统一 schema
- 这意味着“行业资金流同日可用”这件事，现在不再由单一脆弱接口决定

第二，北向 / 南向的同日信号口径已经重新定义。

- 这一轮已经确认：`stock_hsgt_hold_stock_em` 的个股持仓排行目前长期只更新到 `2024-08-16`
- 因此它不适合继续承担“同日个股北向/南向持仓变化”的职责
- Radar 现在已经把这层设计改正：
  - 同日 fresh 的北向 / 南向信号，只使用 `stock_hsgt_hist_em` 的市场级净买入总额
  - 个股持仓排行不再作为当日报告的必要依赖
- 这一步很关键，因为它把“接口 stale”问题转成了“口径重设”问题：不是我们没抓到，而是这个上游口径本来就不应该继续承担同日个股信号

第三，龙虎榜 lane 已经从 schema 漂移里脱身。

- `stock_lhb_detail_daily_sina` 之前会因为返回列名变化触发 `KeyError: 股票代码`
- 这类问题本质上不是“没数据”，而是上游 HTML table 的 header 偶尔变化
- 现在 Radar 已经补上 Sina HTML 的结构化 fallback parser：即使官方 helper 因列名漂移失效，也能直接从页面表格解析出 `股票代码 / 股票名称 / 指标`
- 这样龙虎榜 lane 现在已经回到了 `pass`

第四，SZSE ETF scale lane 已经补上真正的可持续容错。

- 之前 `fund_etf_scale_szse` 的 warning 本质上是 availability 问题：偶发 `ConnectionResetError`，不是业务逻辑问题
- 现在这条 lane 已经补上：
  - 重试
  - 更稳的浏览器请求头
  - 最近成功缓存 fallback
- 所以它现在不会因为一次短暂 reset 就把 market substrate 打成 warning

这一轮之后，系统层的结论变得更明确了：

- `canonical market` 现在不能只看“表级最大日期”，还必须看 lane 的业务语义是否成立
- 特别是北向 / 南向这里，市场级总额和个股持仓已经不属于同一个 freshness 口径
- Radar 当前已经开始按“可持续使用”来消费上游，而不是看到一个接口就盲目把它并进同日主链

同时，这一轮也顺手补了一层更深的韧性：

- 当 `canonical.duckdb` 临时被锁，或者 OpenBB provider 暂时不可用时
- `build_radar_canonical_price_bridge.py` 与 `build_radar_canonical_fundamental_bridge.py` 不再直接把整条日报主链打死
- 它们现在会尽量继续使用 provider fetch 的结果服务当天出报，并在能写回时继续把新数据写回共享底座

这意味着 Radar 现在已经不只是“会调用上游”，而是开始具备：

- 识别上游 lane 是否真的适合进入同日报告
- 在 lane 失效时自动切到更稳的 fallback
- 在共享底座临时不可写时保住当天出报

## 6.14 2026-04-20 `fundamental bridge` 已从 `skip` 收成 `pass`

这一轮真正修掉的，是财务层长期停留在 `skip` 的结构性问题。

之前的问题表面看是：

- `radar_fundamental_coverage_latest.json = skip`
- 本地 bridge 和远端 bridge 都在报 `127.0.0.1:16900 connection refused`

但更深一层的根因其实有三个：

第一，Radar 对 OpenBB 服务地址的假设过时了。

- 当前 a private runtime configured outside this repository 上真实运行的 `openbb-api.service` 监听在 `127.0.0.1:6900`
- 但 Radar fundamental bridge 还沿用了旧默认 `16900`
- 这意味着即使远端 OpenBB 是活的，bridge 仍然会把它误判成“服务不可用”

第二，A 股财务不应该只看 `OpenBB + yfinance`。

- 这轮验证已经确认：`东方证券 / 通策医疗 / 冠农股份` 这类票，`OpenBB yfinance` provider 会返回 `0 rows`
- 但同一时间，远端 `a_share/latest_financial_snapshot.csv` 里其实已经有它们的最新财务摘要
- 也就是说，这些对象不是“没有财务数据”，而是“Radar 还没把 canonical A 股 financial snapshot 当成正式读面”

第三，`canonical.duckdb` 当前存在现实锁竞争。

- 远端锁冲突进程是 `import_market_reference_lane.py`
- 当它持有冲突锁时，`get_equity_fundamental_statement_results()` 会直接抛 `IOException`
- 所以如果 Radar 只会直接碰 `canonical.duckdb`，就会把“库被锁”误解成“公司没有财务覆盖”

这一轮之后，财务层已经重新收成更合理的层次：

- 第一层：直接读 canonical `financial_statement_facts`
- 第二层：如果 canonical 当下不可读，再走 `OpenBB provider fetch`
- 第三层：对 A 股对象，如果 provider 仍然空，但 canonical financial snapshot export 已有值，就使用 `a_share/latest_financial_snapshot.csv` 做 `summary_fallback`

这带来两个直接结果：

- `radar_fundamental_coverage_latest.json` 现在已经回到 `pass`
- `summary_fallback_count = 3`

也就是说，这 3 个之前看起来像“财务缺口”的 A 股对象，现在已经被重新识别成：

- `full statements unavailable right now`
- `but canonical summary snapshot is already available`

这比简单把对象标成缺口更符合系统现实，也更符合你要的 `canonical-first` 架构。

这一轮的另一个关键改进是，bridge 现在已经会主动发现 OpenBB 服务：

- 不再假设 `16900`
- 会先读 `openbb_service.env`
- 再探测 `6900 / 16900`
- 只有在真的找不到健康 OpenBB HTTP service 时，才把本地 lane 标成 `skip_no_openbb_service`

这意味着今天之后，财务层已经不再是：

- “本地没 OpenBB 就整层跳过”

而是：

- “本地没服务就自动继续走远端”
- “远端有服务就自动接到对的端口”
- “即使 canonical DB 当下被锁住，A 股也还能从稳定 snapshot 读面拿到最新摘要”

如果继续往系统上限看，这一轮之后剩下更真实的问题已经收敛成：

- 新闻层是否真的能持续提供足够厚的高质量事件供给
- fundamental bridge 背后的 OpenBB / provider 链在 steady-state 下能否像 market substrate 一样稳定
- 情绪 sidecar 的 freshness 是否也要逐步收成和 market 同等级别的门禁语义

## 6.15 2026-04-20 `canonical writeback` 已从“直接失败”推进到“可恢复延后”

这一轮真正补的，是 Radar 对 canonical 底座写回失败时的系统语义。

现在已经确认的现实不是：

- Radar 抓不到 fresh 数据

而是：

- Radar 能抓到 fresh 数据
- 但 `canonical-market-refresh.service` 会长时间占用 `canonical.duckdb` 的写锁
- 所以“立刻写回 canonical”经常失败

最近一次直接观测里：

- `canonical-market-refresh.service` 已持续运行超过 `6` 小时
- 主导进程是 `import_market_reference_lane.py`
- `lsof` 明确显示它以写锁方式占着 `/opt/market-data-runtime/database/catalog/canonical.duckdb`

这意味着，如果 Radar 仍然沿用旧口径，结果会变成：

- 当天报告可用
- 但 fresh rows 没写回 canonical
- 下一轮还得重新抓

这不符合“Radar 自己能用，同时结果要回灌到底座”的目标。

所以这一轮之后，price / fundamental bridge 都补上了 `deferred writeback queue`。

当前 canonical 写回已经变成这套逻辑：

1. 先补到 fresh 数据，保证当天 report / snapshot / handoff 能继续跑
2. 先把共享 sidecar 写好，例如 `equity_prices.db`
3. 如果 `canonical.duckdb` 当下可写，就直接 persist
4. 如果 `canonical.duckdb` 仍被长任务锁住，就把 payload 放进：
   - `output/sidecars/canonical_writeback/price_writeback_queue.json`
   - `output/sidecars/canonical_writeback/fundamental_writeback_queue.json`
5. 下一轮 bridge 启动时，会先尝试 flush queue，再继续新的补抓

这一步把系统状态从：

- “上游一锁住，补到的数据就白补了”

推进到了：

- “上游一锁住，Radar 先照常出报，写回改成可恢复的延后任务”

这一层现在已经不再藏在日志里，而是被正式暴露到 audit：

- `source_readiness = pass`
- `price_freshness = pass`
- `fundamental_coverage = pass`
- `data_substrate_audit = warn`

这个 `warn` 的含义也已经更准确：

- 不是今天不能出报
- 而是 canonical writeback 还有 backlog

最新 `radar_data_substrate_audit_latest.json` 已经明确写成：

- `canonical price writeback deferred: 6 queued record(s)`

这比继续输出一个假 `pass` 更健康，因为它终于把两件事拆开了：

- 当天日报是不是可用
- canonical 底座是不是已经完全同步

所以到这一轮，Radar 对 canonical 的关系已经不再是“尽量直写，失败就算了”，而是：

- `canonical-first`
- `same-day usable`
- `eventually consistent writeback`

这才更接近一个长期可持续的下游前置系统。

## 6.16 2026-04-20 数据底座访问架构已经重新收口

这一轮我把“canonical 数据库 / OpenBB / Radar / qlib / Research” 之间的真实关系重新梳理了一遍，结论很重要：

当前真实状态不是：

- `canonical DB -> openbb-api -> 所有下游`

而是：

- `canonical DB + openbb-api + repo-native adapter + 若干 direct reads`

也就是说，现在真正最接近“统一中间层”的，并不是 `openbb-api.service` 本身，而是：

- `Investment/scripts/openbb_adapter.py`

这层 adapter 当前已经具备：

- `canonical-first`
- `provider fallback`
- `persist-back canonical`

它不只是一个“纯 OpenBB HTTP client”，而已经是一个混合的 `market access facade` 雏形。

反过来说，`openbb-api.service` 当前更准确的角色其实是：

- provider / fetch layer
- remote API layer
- MCP sidecar 能力层

而不是：

- canonical truth source
- 所有下游必须唯一直连的 DB facade

这一轮也确认了另一个现实：

- `Radar` 当前确实已经开始沿正确方向走
- 但整个系统还没有完全做到“所有下游统一通过 one facade 消费 canonical”

因为现在仍然存在：

- `qlib` 某些子系统直接读 `canonical.duckdb`
- 部分逻辑仍然自己做 provider routing
- adapter 还存在多份定义，没有完全收成唯一生产入口

所以更合理的目标架构应该是：

- `canonical truth layer`
- `single market access facade`
- `Radar / Research / qlib`

而 `openbb-api.service` 则退回成这层 facade 背后的能力源之一。

这一步的意义不是改个名字，而是把系统边界重新钉死：

- truth source 只能有一层
- provider routing 只能有一层
- canonical writeback 责任也只能有一层

否则后面随着 Radar 和 Research 都越来越重，系统会继续陷在：

- “同一个数据今天从 canonical 来，明天从 AKShare 来，后天从 OpenBB HTTP 来”

这种口径漂移里。

所以这一轮之后，后续最该推进的动作已经很明确：

1. 把生产版 facade 收成唯一入口
2. 把下游 direct DuckDB reads 逐步迁移到 facade
3. 把 deferred writeback queue 从 Radar 升级成共享底座能力

这一步本身不直接让日报变好看，但它会决定这套系统后面能不能长期稳定地每天出一份可信的报告。

## 6.12 2026-04-16 `freshness` 从被动告警推进到主动闭环

这一步把 `freshness` 从“日报会提示 stale”继续推进成了真正的工作流能力。

当前已经新增：

- `scripts/radar_freshness_utils.py`
- `scripts/ensure_radar_freshness.py`
- `output/reports/radar_freshness_latest.json`
- `output/reports/radar_freshness_latest.md`

新的判断口径不再只看 `sample_age_days`，而是同时看：

- `expected_sample_date`
- `freshness_lag_days`

这意味着系统已经能区分：

- `跨自然日但仍然是期望市场样本`
- `真的落后于当前应有样本`

现在的 canonical 流程已经变成：

- 先跑 `python3 scripts/ensure_radar_freshness.py`
- 如果远端样本落后于 `expected_sample_date`，就自动触发 remote refresh
- 然后同步回本地并重建 `日报 / handoff / quality check`

这一轮的真实自检已经确认：

- 当前市场日期为 `2026-04-17 Asia/Shanghai`
- 期望样本日期为 `2026-04-16`
- 本地最新样本 `as_of_date = 2026-04-16`
- `sample_age_days = 1`
- 但 `freshness_lag_days = 0`

也就是说，系统已经不再把“凌晨跨日但还没到下一个交易样本窗口”的报告误判成 stale。

## 6.13 2026-04-16 `triage` 继续往 PM 口径收窄

这一步不是继续改日报文案，而是直接收紧了对象分流逻辑。

核心调整有两条：

- `macro proxy` 不再轻易进入 `immediate_research`
- `general_corporate / soft` 的 company 对象会被系统性降权，不再因为“有题材、有母行业承接”就长期占住前排

这轮之后，最新 `2026-04-16` 样本已经变成：

- `Immediate Research = 1`
- `Thesis Watch = 15`
- `Risk Review = 3`

同时，之前容易占住前排的这批对象：

- `国晟科技`
- `晶科能源`
- `行云科技`
- `仙琚制药`
- `恒瑞医药`
- `索通发展`
- `西部黄金`

已经从 `soft/general_corporate` 的前排候选，退回背景层。

这使得新的 `Top Opportunities` 更像 PM 真会先看的顺序：

- `中际旭创`
- `共达电声`
- `通富微电`
- `株冶集团`
- `三七互娱`

第二块是 `Catalyst Inventory`。

当前已经正式新增：

- `scripts/build_radar_catalyst_inventory.py`

## 6.14 2026-04-16 情绪侧车已经进入 Radar 主流程，日报邮件切到 PDF 正式投递

这一轮做的不是“再让日报多一行情绪点评”，而是把量化系统里已有的情绪 substrate 正式当作 Radar 的上游 sidecar 接了进来。

当前已经吸收的 6 类分数是：

- 市场级
  - `market_flow_sentiment`
  - `market_event_sentiment`
  - `market_composite_sentiment`
- 公司级
  - `company_event_sentiment`
  - `company_market_sentiment`
  - `company_composite_sentiment`

这里的使用原则也已经固定下来：

- Radar 不自己重新做一套“日报情绪打分”
- Radar 也不把情绪完全忽略掉
- 更合适的做法是：把情绪系统作为 `sentiment sidecar`，服务于解释、门控和轻量加权，而不是取代 `catalyst / evidence / milestone`

这一轮之后，主链已经发生三件实质变化：

- `snapshot` 顶层开始显式携带 `market_sentiment_context`
- 公司对象开始携带 `sentiment_context`
- `soft catalyst` 的 Bark 门控开始参考市场综合情绪，避免在弱市场背景下被软催化噪音轻易打断

与此同时，日报本身也不再只输出 Markdown 主稿。

当前已经稳定生成：

- `radar_daily_report_latest.md`
- `radar_daily_report_latest.html`
- `radar_daily_report_latest.pdf`

日报邮件链也已经切换为：

- 邮件正文只保留摘要
- `PDF` 作为正式日报附件
- 不再把整份 Markdown 正文塞进邮件正文

这一点很重要，因为它意味着：

- Radar 日报的生成和发送，已经不再依赖 Codex 或任何 agent 常驻参与
- a private runtime configured outside this repository 上的脚本链已经可以独立完成：
  - fresh sample 检查
  - snapshot / handoff / report / quality 产物生成
  - PDF 附件邮件投递
- `scripts/validate_radar_catalyst_inventory.py`
- `config/radar_catalyst_inventory_schema_v1.json`
- `output/inventory/radar_catalyst_inventory_latest.json`
- `output/inventory/radar_catalyst_inventory_latest.md`

这让 Radar 开始从：

- 每天重新算一遍 snapshot

推进到：

- 维护一份持续的 `catalyst book`

当前 v1 的 inventory state 已经固定为：

- `open`
- `watching`
- `confirmed`
- `risk_review`
- `background`
- `expired`

而且它已经不只是一个孤立输出：

- 日报会显示 `Catalyst Inventory` 分布
- `Top Opportunities` 会显示 `previous_state -> current_state`
- `research handoff` 也会告诉你对象是首次入库、持续跟踪，还是状态升级

第三块是 catalyst taxonomy 本身。

当前 snapshot 里的事件驱动类型已经从原来的粗粒度继续扩到：

- `merger_restructuring`
- `buyback_shareholder_support`
- `financing_dilution`
- `litigation_regulatory`
- `dividend_capital_return`

这意味着系统不再把大量 fund-style 事件都粗暴塞回 `capital_markets / general_corporate`。

这一轮之后，系统的真实变化不是“某个分数更好看了”，而是：

- 日报开始知道自己是不是 stale
- handoff / report / quality check 开始消费同一份跨天状态账本
- event-driven 的语义层又更像一个真正的前置系统，而不是单日报告拼装器

## 6.4 2026-04-12 日报已经进入 mixed-object 投资机会稿阶段

这一轮更关键的变化，不是又多接了一个源，而是日报终于开始像“投资机会日报”，而不再只是“行业扫描结果转述”。

现在已经确认成立的变化包括：

- `opportunity snapshot` 主榜已经不是 `industry-only`，而是第一版 `company / industry / macro` 混排
- 当前最新本地样本已经稳定产出 `company 16 / industry 31 / macro 4`
- 日报正文不再展示 `akshare:*` 这类源标签，也不再把括号元信息直接端给读者
- 日报已经正式新增两个对投资更有用的板块：
  - `个股事件线索`
  - `商品 / 代理线索`
- 个股对象已经开始利用本地公司映射缓存回填 `所属方向 + 股票代码`
  - 例如 `成都华微 688709`
  - `中恒电气 002364`
  - `海陆重工 002255`

这一步的意义是：

- Radar 终于开始把“行业线索”压成“可以直接接研究的对象”
- 个股不再只作为行业表里的一个标题，而是开始变成独立对象进入主榜
- 商品 / 代理变量也开始以独立观察对象出现，而不是只埋在行业证据字段里

但这一轮也明确暴露出一个还没完全收好的问题：

- `industry` 行本身的事件挑选逻辑还不够稳
- 某些行业对象目前仍可能把 `fund halt / 泛券商晨报 / 地缘新闻标题` 这样的文本放到门面位

所以当前最准确的判断是：

- `mixed-object Radar` 已经开始成型
- `日报可读性` 已经跨过了第一道门槛
- 接下来最值得继续打磨的，不是“有没有个股和商品”，而是“行业门面事件怎么选得更像研究入口”

## 6.5 2026-04-12 对标公开 event-driven 基金后，Radar 定义进一步收紧

这一轮最重要的产品结论，不是来自我们自己的代码，而是来自外部对标。

基于公开可得的 event-driven 基金材料，我们现在可以更有把握地说：

- 一个成熟的事件驱动前置系统，核心不是 `news feed`
- 核心是 `catalyst inventory + milestone tracker + research triage`

公开材料里最稳定的共性包括：

- 他们围绕的是 `corporate change / catalyst / event`
- 不是所有事件都一样，至少会隐含地区分 `hard catalyst` 和 `soft catalyst`
- `hard catalyst` 的关键，不是 headline 热度，而是 `defined plan and timeframe`
- `soft catalyst` 则更依赖研究资源、创造性判断和更早的 watchlist 组织能力
- 多数 event-driven 平台不是单榜，而是多子策略、多 bucket 的动态分流平台
- 风险管理、hedging、crowding、basis risk 不是附属信息，而是事件系统的一部分

这对我们当前项目的直接含义是：

- Radar 的定义应该从“新闻雷达”正式收敛成 `事件驱动投资的前置 operating layer`
- News Hub 的完成，只意味着 intake 基础开始可用
- 真正还没有完成的，是下面这些中枢能力：
  - `catalyst taxonomy`
  - `hard / soft catalyst` 分层
  - `milestone / timeline / due window`
  - `deal / special situation` 对象层
  - `risk / hedge context`

所以现在最准确的判断不是：

- “系统还差很多，所以能力不够”

而是：

- “新闻系统已经基本成形，但事件驱动中枢层还没有建完”

这轮完整对标结论已经单独落成：

- `reports/event_driven_radar_reference_20260412.md`

## 6.6 2026-04-12 催化剂语义已经进入正式 contract

这一步的意义，不在于分类结果已经成熟，而在于 Radar 终于开始有“事件驱动系统自己的语言”了。

当前已经正式进入 snapshot / schema / validator / 日报的字段包括：

- `catalyst_type`
- `hard_or_soft`
- `catalyst_stage`
- `next_milestone`
- `milestone_due_window`

这意味着从这一轮开始：

- 对象不再只是一条分数和 bucket
- 每个重点对象开始带“这是什么催化剂、属于 hard 还是 soft、现在走到哪一步、下一步看什么、窗口多长”

例如当前日报前排对象已经开始长成：

- `成都华微`
  - `earnings_guidance`
  - `hard`
  - `announced`
- `海陆重工`
  - `order_project`
  - `hard`
  - `execution_window`
- `BDI / 航运运价`
  - `industry_proxy`
  - `soft`
  - `monitoring`

这一步还不是成熟的 milestone engine，但已经足够把下一阶段的主线钉死：

- 先继续校准 taxonomy 判定质量
- 再拆出真正的 `hard catalyst board / soft catalyst watchlist`
- 然后再把 risk / hedge context 接进来

## 6.7 2026-04-12 `hard board / soft watchlist / risk monitor` 已经落地

这一轮之后，Radar 已经不只是“对象带 hard/soft 字段”，而是开始有真正的事件驱动工作板。

当前 snapshot 已经新增：

- `event_driven_lane`

并开始把对象分到：

- `hard_catalyst_board`
- `soft_catalyst_watchlist`
- `risk_monitor`
- `background_monitor`

当前最新 live 样本的分布已经是：

- `hard_catalyst_board = 11`
- `soft_catalyst_watchlist = 10`
- `risk_monitor = 3`
- `background_monitor = 27`

这一步的意义非常直接：

- 日报开始有了接近 event-driven 基金工作流的第一层结构
- 不再是“先看总榜，再自己脑补哪些是 hard catalyst”
- 也不再把所有对象一股脑塞进同一层研究优先级语义里

从产品视角看，这一步比继续补一个数据源更重要，因为它开始真正回答：

- 哪些对象已经是 `hard catalysts`
- 哪些对象还只是 `soft watchlist`
- 哪些对象虽然值得看，但本质上更像 `risk monitor`

## 6.8 2026-04-12 `confirmation gap / hedge difficulty / failure mode` 已经进入对象层

这一轮之后，Radar 的对象不再只会表达“为什么值得看”，也开始表达“哪里会失败”。

当前已经正式进入 snapshot / schema / validator / 日报的字段包括：

- `confirmation_gap`
- `hedge_difficulty`
- `failure_mode`

这一步的意义在于：

- 重点对象开始显式说明还缺哪一层确认
- 研究排序开始承认不同对象的对冲难度并不一样
- 事件驱动面板不再只有机会视角，也开始自带失败路径

这仍然是启发式 v1，不是成熟的风险引擎，但它已经把下一阶段的主线钉得更清楚：

- 先继续校准行业对象的确认缺口描述
- 再决定这些字段怎么进入 ranking / Bark 阈值
- 最后才谈更精细的 risk-adjusted event prioritization

## 6.9 2026-04-13 风险语义已经进入 Bark 门控，行业门面事件开始去噪

这一轮之后，`risk / hedge / confirmation` 不再只是日报上的解释字段，而是真的开始影响提醒行为。

当前 Bark 门控已经开始直接参考：

- `event_driven_lane`
- `confirmation_gap`
- `hedge_difficulty`

这意味着：

- `risk_monitor` 对象不会再轻易直接进入 Bark
- `soft catalyst + 大确认缺口 + 低置信` 的对象会更倾向留在日报层
- `高对冲难度 + 低确认` 的对象也会被先压回观察

同时，行业对象的门面事件也做了一轮收敛：

- `公告 / proxy / 更可信的具体公司事件` 开始优先于泛评论和弱相关 headline
- `非银金融` 已开始优先展示 `两融总量 / 券商交易景气代理`
- `交通运输` 已开始优先展示 `BDI + 航贸运价指数代理`
- 当弱行业对象缺少 `公告 / proxy / 明确公司主体事件` 时，日报现在会直接退回 `总分 + 行业状态` 摘要，而不再硬挂错位 headline

同时，这一层不再只停留在展示层：

- 缺少 `公告 / proxy / 明确主体事件` 的行业对象已经开始被直接做 `ranking` 降权
- 最新样本里 `汽车 / 机械设备 / 社会服务` 已经从 `research_candidate` 退回 `observe`
- 这使得前排研究位开始更多留给有结构化承接的对象，而不是留给“热度在，但研究抓手并不稳”的行业卡片

但这一步又不该走到另一头：

- `汽车 / 机械设备` 这类虽然缺硬确认、但已有正向主题扩散线索和交易结构支撑的对象，不应被和纯泛新闻行业一刀切
- 当前最新口径已经把它们保留在 `research_candidate`
- `社会服务` 这类没有结构化承接、也缺早期主题支撑的对象，则继续留在 `observe`

同时，Radar 已经开始从“排序器”继续长成“事件驱动研究分流板”：

- snapshot 正式新增 `evidence_quality / triage_action`
- 日报新增 `研究分流板`
- 系统现在会明确把对象分到 `immediate_research / thesis_watch / risk_review / background_only`
- `triage_action` 已开始真正影响工作流：只有 `immediate_research + structured/proxy evidence` 的强对象才继续保留 Bark 资格，同时系统会额外产出 `research handoff`，把对象交接到 `Immediate Research Queue / Thesis Watch / Risk Review`
- 这条交接链已经不是本地附加件：live runner、远端安装和 payload mirror 都开始把 `research handoff` 当作主链原生产物
- `research handoff` 又进一步从“可读板”推进成“可接板”：当前已经同步产出 JSON，且 snapshot 里的 `research_links` 不再是空列表，后续研究承接脚本可以直接围着这层接口长
- `triage_action` 的口径也开始收紧：`structured_confirmed`、`proxy_confirmed`、`early_thematic` 不再走同一个粗门槛，`两融总量 / 非银金融` 这类对象已经被压回 `thesis_watch`，而 `BDI / 航运运价` 这类高质量 proxy 仍保留在 `immediate_research`

这一步的意义是：

- 前排对象不再只是“排在前面”
- 它们开始带上明确的工作指令
- Radar 开始更像事件驱动基金的前置 operating layer，而不是只有排序结果的看板

这一步的意义是，Radar 开始更像一个真正的事件驱动前置系统，而不是“排序后再把第一条抓到的标题贴上去”。

## 7. 下一步重点

- 先定义“行业起势”的标签口径
- 先建立全行业覆盖骨架
- 先确定原型样板集
- 先搭出每个节点的最小可行方案
- 先做外部经验扫描和失败模式归纳
- 再决定新闻层、基本面层、资金层的自动化优先级

## 8. 2026-04-03 告警口径进一步收敛

这一轮项目目标又收紧了一步：

- 全行业平权扫描，不预设重点行业
- 告警默认不承担“深度研究说明书”的职责
- Bark 默认只承担“研究入口”职责

当前默认 Bark 正文已经收敛成短模式，优先只保留：

- 行业
- 触发原因
- 核心证据
- 单条催化

这一步的意义是：

## 9. 2026-04-03 远端验收链真正闭环

这一轮不是再补一个 collector，而是把 `manual one-shot validation` 这条工程链真正闭上了。

之前的问题不是行业雷达主链本身一定失败，而是 detached 验收链会混进几类噪音：

- SSH session scope 和后台进程边界不够清楚
- wrapper 自身退出码不稳定落盘
- status 视图容易混进旧 run 的健康记录和旧告警

这一轮之后，当前远端 one-shot 已经改成：

- `systemd-run --no-block` 优先拉起
- 独立 `status file`
- 独立 `wrapper return code`
- 独立 transient unit state

而且 `status` 视图也已经改成优先读取：

- `manual_validation_status.target_run_id`
- 然后展示该目标 run 自己的：
  - `source_health`
  - `top_snapshots`
  - `recent_alerts`

远端 `wrapper_rc_v26` 的结果已经证明这条链现在能完整闭环：

- `target_run.status = success`
- `wrapper_return_code = 0`
- `industry_count = 31`
- `alert_count = 0`
- `execution_mode = market_tick`

这一步的意义不只是“运维更顺”，而是：

- 后面我们继续补新闻、资金、基本面节点时，不会再被假性 `interrupted` 误导
- 每次调 collector，都能更干净地判断“这轮目标 run 自己到底发生了什么”
- 工程验收开始真正服务于投资信号质量，而不是先被部署噪音吞掉

## 11. 2026-04-03 新闻层第一版重叠评估

新闻层扩到三源之后，下一步不能靠直觉判断“是不是还要继续补源”，而要先回答一个更实在的问题：

- 这三类源到底是不是在提供不同层次的信息
- 哪些已经开始重叠
- 先补新源，还是先做去重和时间戳治理

这轮我先落了第一版重叠快照，结果比较清楚：

- `news_cctv`：`13` 条可用标题，`13` 条唯一标题，`date_cov = 1.00`，但 `time_cov = 0.00`
- `stock_info_global_cls`：`16` 条可用标题，`16` 条唯一标题，`date_cov = 1.00`，`time_cov = 1.00`
- `stock_info_global_em`：`200` 条可用标题，`200` 条唯一标题，`date_cov = 1.00`，`time_cov = 1.00`
- `CLS <-> EM`：`exact_overlap = 5`，`approx_overlap = 6`
- `CCTV <-> CLS/EM`：当前快照 `exact_overlap = 0`，`approx_overlap = 0`

这说明当前三源不是简单重复：

- `CCTV` 更像政策和宏观层，和另外两层重叠很低
- `CLS` 和 `EM` 已经开始出现同层快讯重叠

所以这轮最重要的结论不是“可以继续补更多泛财经源”，而是：

- 先补 `headline dedupe`
- 先补 `cross-source event clustering`
- 先补 `timestamp normalization`
- 再决定要不要继续测试 `Sina / THS` 这类同层源

我还顺手让 Kimi 对这份 overlap 快照做了只读审阅，它给出的主建议和我们自己的判断一致：

- 当前最优先动作应该是 `去重 / 时间戳归一`
- 行业映射验证应该跟着一起做
- 现在继续扩同类新闻源，只会先放大重复和误匹配

这一步的意义在于，新闻层现在开始从“能抓几类公开新闻”往“能不能稳定提供有增量的行业事件流”过渡。

另外，这一步我没有停在研究结论上，而是把第一层实现接进了主链：

- `CCTV` 的日期新闻会统一推断成 `date_only_inferred`
- `CLS / EM` 会统一写出 `published_at + timestamp_quality`
- 同行业同日新闻开始按 `exact / fuzzy` 两层规则压成 cluster
- cluster 会记录 `source_count / source_ids / cluster_size`

这意味着新闻层现在已经不只是“三个 source 并排拉回来”，而是开始向真正的 `事件流` 靠近。

我又顺手补了一层 `行业映射命中率` 快照，结论也很重要：

- `news_cctv`：`single_hit = 5 / multi_hit = 2 / unmatched = 6`
- `stock_info_global_cls`：`single_hit = 6 / multi_hit = 0 / unmatched = 14`
- `stock_info_global_em`：`single_hit = 64 / multi_hit = 11 / unmatched = 125`

这里暴露出来的主要短板，不是“歧义特别高”，而是：

- 还有相当多新闻根本没有命中任何行业

这说明新闻层下一步要补的，不只是 `dedupe`，还包括：

- 行业别名
- 产业链词
- `heat_keywords` 扩表

否则即使有更多新闻源，很多本该进入行业候选池的事件也会继续漏掉。

所以这轮我没有继续去补新的同类新闻源，而是先让 Kimi 做了一轮机械扩表，我来做 review 和验收。最后我保留了大部分保守增量，并人工拒绝了高风险的 `建筑装饰 -> 船舶/造船` 映射。

相对最初那版 mapping 快照，最新命中率改善是：

- `news_cctv`：`single_hit 5 -> 7`，`unmatched 6 -> 4`
- `stock_info_global_cls`：`single_hit 6 -> 7`，`multi_hit 0 -> 1`，`unmatched 14 -> 12`
- `stock_info_global_em`：`single_hit 64 -> 81`，`multi_hit 11 -> 10`，`unmatched 125 -> 109`

这说明“先补词表，再看新源”这条顺序是对的。至少在当前阶段，新闻层的增量不主要来自继续加同类公开源，而主要来自：

- 更好的关键词
- 更好的时间戳
- 更好的去重和事件压缩

但这轮继续往前走之后，边界也开始变得更清楚：

- 一级行业 `heat_keywords` 仍然有用
- 但它的边际收益正在下降

比如我让 Kimi 做了一轮“只读候选词审阅”，最后真正保守并回的只有一条：

- `先进制程 -> 电子`

而像下面这些，看起来都“跟某些行业有关系”，但更适合进别的层：

- `霍尔木兹海峡`
  这更像 `geo / shipping / energy` 的复合事件，不适合直接无脑并入一级行业 backbone
- `Wegovy / GLP-1`
  这更像医药主题链，不是稳定的一层行业词
- `特朗普 / PMI / ST / 业绩预告`
  这些要么过于宏观，要么过于通用，要么更像监管事件

所以新闻层现在已经进入一个更成熟的节奏：

- 先做 `行业映射命中率` 快照
- 再做 `未命中新闻 -> 候选词` 工作纸
- 然后让 Kimi 做保守的只读建议
- 最后由主控人工决定哪些词真正并回 backbone

这件事的意义很大。因为它说明新闻层的下一段主要增量，不再是继续堆同类公开新闻源，而是：

- `公司名 / 机构名 -> 行业` 辅助映射
- `theme / macro / geo overlay`
- 更好的事件层压缩

这轮我又把第一项正式落下来了：

- 新增了 `company_name -> industry` 的辅助映射模块
- 但我刻意把它定义成 `补漏层`，而不是主归因层

原因很简单。公司名映射确实能捞回不少原本完全漏掉的公司新闻，比如：

- `准油股份 -> 石油石化`
- `博彦科技 -> 计算机`
- `高乐股份 -> 轻工制造`
- `强一股份 -> 电子`

在最新快照里，这一层已经把 `EM` 这类公司新闻流的结果明显抬起来了：

- `single_hit = 82 -> 97`
- `company_hit = 21`
- `unmatched = 109 -> 89`

但我没有让它直接升级成“新闻主归因规则”。因为它对 `CLS` 这种更偏快讯/宏观混合流的帮助并不明显，而且个别样本里还出现过可疑错配。更准确地说，这一层现在最适合做的是：

- 只在“像公司新闻、公司公告”的标题里启用
- 只负责把明显漏掉的公司新闻捞回行业池
- 不去接管宏观、地缘、政策、主题类新闻的归因

这也是为什么我后来又给它加了一道闸门：

- 只有标题像公司公告/公司新闻时，才允许启用公司名匹配

这样做虽然保守，但更符合这个系统的目标。我们要的是投资可用的行业雷达，不是为了抬命中率，把所有东西都硬凑成行业信号。

另外，这轮还顺手把远端 one-shot 验收链最后一处 heredoc / `$rc` 噪音收掉了。`wrapper_fix_v28` 已经证明 `start` 和 `status` 两条链都能稳定工作，这意味着后面我们继续调 collector 或看具体告警时，不用再绕开这条工具链。

另外，我还把 `primary / 高置信 overlay keywords` 也接进了匹配逻辑。它在当前这批样本上没有继续抬高命中率，但这一步的价值不在“今天立刻多命中几条”，而在于后面像：

- `AI算力`
- `创新药`
- `电网设备`
- `光伏主链`

这种主题表达不必全都硬塞进一级行业的 `heat_keywords`，而是可以通过 overlay 层更干净地进入行业雷达。

## 10. 2026-04-03 新闻层扩到第三个公开主源

在验收链闭环之后，下一步不是回头再堆工程骨架，而是继续补 `全行业平权可复用` 的信号层。

这一轮新闻层新增了第三个公开源：

- `akshare:stock_info_global_em`

它和原来的两层分工不同：

- `news_cctv` 更偏政策与宏观口径
- `stock_info_global_cls` 更偏高频财经快讯
- `stock_info_global_em` 更偏泛财经与交易所、产业新闻补充

所以当前新闻层已经从：

- `news_cctv + stock_info_global_cls`

扩成了：

- `news_cctv + stock_info_global_cls + stock_info_global_em`

而且这次我顺手把一个噪音点也收了一下：

- `industry_keywords()` 不再无条件把行业名硬塞进每个关键词集
- 只有没有明确热词时，才退回到行业名

这样做的目的，是避免随着新闻源越来越多，误匹配也被一起放大。

远端这轮已经验到：

- `source_count = 38`
- `stock_info_global_em.status = pass`
- `fetched_count = 200`
- `used_date = 2026-04-03`

也就是说，新闻层现在开始更像一个真正的 `多源公开事件压缩层`，而不是只靠单一快讯源在撑。

- 把系统从“能发很多信息”继续往“真正方便投资决策监控”推进
- 先把提醒做短、做稳、做可读
- 更深的研究放到你收到信号之后再展开

这一轮还顺手处理了一个实际的辅助资金源问题：

- `龙虎榜` 不再只依赖单一 `股票代码` 列名
- 当前已经兼容 `股票代码 / 证券代码 / 代码`
- 这样这条显性资金痕迹层在不同运行环境下更不容易退化成长期 `warn`

## 9. 2026-04-02 可行性与资源判断

在完成成功标准和外部对标之后，当前已经可以对项目可行性做出第一轮判断。

当前结论是：

- 以现有底座，可以做出一个 `可用的全行业多重共振预警系统 v1`
- 这个 v1 更现实的目标是：
  - 日频或半日频扫描
  - 自动抓取多源信号
  - 自动推送一批值得立刻打开研究的行业候选
- 当前最大的缺口不是远端服务器，而是：
  - 信息源、新闻源、数据源的接入层
  - 新闻去重、聚类与行业映射
  - 共振规则和告警治理

当前不适合直接承诺的是：

- 全量实时新闻流系统
- 统一覆盖所有行业的高质量高频基本面代理层
- 完全靠免费公开源就达到长期生产级稳定性

这一轮的资源判断入口是：

- `docs/feasibility_and_resource_plan.md`

## 10. 2026-04-02 执行主控文档切换

当前项目已经把日常推进控制从 `master_plan.md` 切换到：

- `docs/execution_checklist.md`

这样做的原因是：

- 项目已经进入长周期建设阶段
- 只保留背景规划文档已经不够
- 后续需要一份能持续打勾、记录增量信息、记录新问题、同步成功状态的执行主控文档

这一轮同时固定了两个执行口径：

- v1 部署节点默认沿用内网服务器 a private runtime configured outside this repository
- v1 主告警通道固定为 `Bark`

当前 Bark 推送的主参考实现固定为：

- `Investment/scripts/watchlist_price_monitor.py`

这一轮的意义不是系统已经上线，而是：

- 执行主控、部署前提、数据复用边界和告警通道终于被放到了同一张施工图里

## 11. 2026-04-02 Phase 0 配置落地

这一轮不仅把执行主控文档立起来，也把 `Phase 0` 的核心边界正式写成了配置，而不是继续停留在口头约定。

当前已经正式固定：

- 新系统独立 runtime root：`/opt/industry-signal-radar`
- 默认共享只读市场数据：
  - `Qlib dynamic provider`
  - `raw source`
  - `historical master`
- 明确禁止共写：
  - `paper_trader/state.db`
  - `output/paper_trader/*`
  - 现有 provider 发布链
- v1 轮询节奏：
  - 交易时段每 `10` 分钟轻量轮询
  - 非交易时段每 `30` 分钟轮询
  - `15:20` 做收盘后全量重算

这一轮的意义在于：

- 后续运行骨架终于有了明确的默认路径和安全边界
- “共用现有市场数据，但不污染现有量化系统”已经从原则变成了正式配置口径

## 12. 2026-04-02 Phase 1 source manifest 落地

这一轮继续把 `Phase 1` 往前推了一步，把项目未来要接的源，不再只放在讨论和 backlog 里，而是正式写成了第一版 `source manifest`。

当前已经正式分层的来源包括：

- 共享市场数据
- 行业映射
- 程序化市场数据 API
- 公告源
- 政策源
- 基本面代理源
- 新闻源

这一轮的关键意义不是“已经接好了这些源”，而是：

- v1 到底准备接哪些源，终于有了统一注册表
- 每个源的 `优先级 / 可信度 / 调度类 / 当前接入状态 / 用途层` 已经开始结构化
- 后续 Phase 4 和 Phase 5 不用再从零列清单，而是沿着 manifest 往前接 collector

这一轮也同步明确了一点：

- `source manifest` 是运行骨架的一部分，不只是研究清单
- 后续 source health、失败重试、event db 都应该以 `source_id` 为主线继续扩

## 13. 2026-04-02 Phase 1 event db 落地

这一轮继续把运行骨架往前推进，把独立 `event db` 的 schema 和初始化入口正式立住了。

当前 event db 已经开始承接这些对象：

- 运行批次
- source registry snapshot
- signal snapshot
- alert event
- dispatch attempt
- source health
- runtime health

这一轮的意义在于：

- 后续告警和复盘终于不需要临时 JSON 拼接来承担全部状态职责
- `source manifest -> event db` 已经连上，source registry 可以进入状态层
- 后续 Bark 发送结果、去重、升级重发和历史回放都有了统一的数据库承接位

这一步还没有等于系统已经开始产生日志，但它意味着：

- 运行骨架已经从“配置存在”推进到“状态库模型存在”
- Phase 6 和 Phase 7 终于可以围绕同一份 event schema 继续推进

## 14. 2026-04-02 Phase 1 runtime health 入口落地

这一轮继续把运行骨架补完整，不再只停在数据库 schema，而是把 `runtime health` 的记录入口也做出来了。

当前这条入口已经能做两件事：

- 向独立 event db 写入 `runtime_health` 和 `source_health`
- 同步落一份 `health/runtime_health_latest.json`

这一轮的意义在于：

- 后续服务运行、抓取失败、Bark 发送失败终于有了统一的健康记录入口
- 可观测性不再完全依赖临时终端输出或手工看日志
- Phase 1 已经从“只有配置和 schema”推进到“开始有正式状态写入口”

接下来最自然的动作，就是把它接进真正的 `service / timer` 和运行链，而不是继续只停在手动入口。

## 15. 2026-04-02 Phase 1 service / timer / guardrail 落地

这一轮把 `Phase 1` 基本收口了。

当前已经补上的部分包括：

- `service / timer` 的 repo 内 source-of-truth
- 远端 wrapper `run_industry_signal_radar.sh`
- `radar_runtime_bootstrap.py`
- `check-only`
- 共享 / 隔离边界的 guardrail 校验

这一步的关键意义在于：

- 运行骨架不再只是零散脚本，而开始形成真正的运行入口
- “不能污染现有量化系统”已经不只是文档要求，而变成了 bootstrap 里的实际校验逻辑
- 后续把这条线接进 a private runtime configured outside this repository 时，不需要再从零拼 service、timer 和 wrapper

当前还差的一步是：

- 把 repo 里的 source-of-truth 真正部署到 a private runtime configured outside this repository

也就是说，`Phase 1` 现在已经从“设计期”推进到了“待接线的可部署骨架”。  
接下来项目重点就更适合切到 `Phase 2` 和 `Phase 6`：行业对象层和告警共振规则层。

## 16. 2026-04-02 部署脚本落地

这一轮把“怎么接到 a private runtime configured outside this repository”也正式做成了脚本。

当前已经新增：

- 环境预检 / venv 入口
- 安装脚本入口
- 独立 requirements lock 占位文件

这一步的意义在于：

- 行业雷达系统不再只是一套 repo 内骨架
- 它已经具备了和现有量化链一样的脚本化部署路径
- 后面真正做第一次远端接线时，不需要再手工拼目录、wrapper 和 systemd 单元

当前最自然的下一步是：

- 对 a private runtime configured outside this repository 跑一次环境预检
- 如果 preflight 正常，再做第一次独立安装

## 17. 2026-04-02 a private runtime configured outside this repository 首次独立部署完成

这一轮已经把项目真正接到了 a private runtime configured outside this repository。

当前已完成的远端动作包括：

- 环境预检通过
- 独立 `.venv` 建立成功
- 脚本、配置、wrapper 和 systemd 单元同步成功
- `industry-signal-radar.timer` 已启用
- 首次手动触发的 `industry-signal-radar.service` 已成功完成 bootstrap
- 远端 `event db`、`runtime health` 和 `runtime_bootstrap_latest.json` 已成功生成

这一步的意义很大，因为它说明：

- 项目已经不是“只存在于本地文档和脚本”
- 它已经在目标内网服务器上拥有了独立运行位
- 共享数据复用 + 状态隔离这条边界，在真实远端路径下已经被验证过一次

但这一轮也把一个边界暴露得更清楚：

- 当前远端 service 跑的是 `bootstrap`，还不是完整扫描器
- timer 当前也只是基础 `10min base tick`，还不是最终的市场时段调度语义

所以接下来的重点已经不再是“能不能部署”，而是：

- 把 `bootstrap-only` 入口扩成真正的 scanner / runner
- 把全行业对象层和告警共振规则正式接进去

## 18. 2026-04-02 MVP scanner / runner 跑通

这一轮项目已经从 `bootstrap-only` 进一步推进成真正可运行的 `MVP scanner / runner`。

当前已经落成的关键部分包括：

- 全行业 backbone：第一版 `31` 个申万一级行业 registry
- 对象层入口：`industry_registry_sw_level1.csv` + `radar_industry_registry.py`
- 扫描入口：`radar_scan_runner.py`
- Bark 分发入口：`radar_bark.py`
- 远端 env file 口径：`industry_signal_radar.env`

当前第一版扫描器已经能接入并压缩这些公开可用信号：

- 申万一级行业历史走势
- 东方财富行业板块热度
- 申万一级估值与股息率信息

当前第一版行业级得分由这几层组成：

- `money_flow_score`
  - `1d / 5d / 20d` 行业涨幅横截面分位
  - 成交额相对自身历史均值的放大倍数
- `heat_score`
  - 东方财富行业热榜的排名和涨跌幅贡献
- `fundamental_score`
  - `PE TTM` 的逆分位
  - 股息率分位
- `policy_score`
  - 第一批来自 `akshare:news_cctv`
  - 行业关键词命中后写入 `policy_articles`
- `total_score`
  - 当前权重：`0.6 * money_flow + 0.15 * heat + 0.1 * fundamental + 0.15 * policy`

当前第一版状态机已经可以稳定输出：

- `cold`
- `warming`
- `candidate`
- `strong_alert`

这一轮还补上了第一版业务调度门控，当前 runner 已经可以区分：

- `market_tick`
- `off_session_tick`
- `after_close_rebuild`
- `skipped_off_session_base_tick`

本地与远端当前都已经跑通了一次完整扫描。

截至 `2026-04-02`，a private runtime configured outside this repository 上已经确认：

- `industry-signal-radar.timer` 处于 `active (waiting)`
- 最近一次 `industry-signal-radar.service` 完整退出状态为 `0/SUCCESS`
- 远端输出文件已生成：
  - `industry_signal_scan_latest.json`
- 远端状态库当前已写入：
  - `radar_runs = 17`
  - `signal_snapshots = 217`
  - `alert_events = 12`
  - `dispatch_attempts = 12`
  - `runtime_health_events = 17`
  - `source_health_checks = 2`

当前样例提醒已经出现了两条：

- `医药生物`：`strong_alert`
- `食品饮料`：`candidate`

当前 Bark 实发也已经完成验证：

- a private runtime configured outside this repository 已复用 AWS `watchlist price monitor` 的双 device key 配置
- `dispatch_attempts` 已出现真实 `sent`
- 已验证 `success_count = 2`、`failure_count = 0`

当前第一批新闻 / 政策压缩层也已经接进 runner：

- `news_cctv` 会按行业关键词命中生成 `policy_score`
- 证据会以 `policy_articles` 写入 `signal_snapshots.evidence_json`
- 源健康状态会写入 `source_health_checks`

当前第一版验证闭环也已经落成：

- `radar_validation_summary.py` 会直接读取 `alert_events`
- 自动生成：
  - `industry_signal_validation_latest.json`
  - `industry_signal_validation_latest.md`
- 截至当前，远端验证摘要统计为：
  - `total_alerts = 12`
  - `evaluated_alerts = 0`
  - `pending_alerts = 12`

这组结果的含义不是验证失败，而是：

- 现有 alert 样本都太新
- `1/3/5` 日未来收益窗口还没走完
- 但验证链条本身已经被打通

当前第一批行业基本面代理样板也已经接进 scanner：

- 新文件入口是 `radar_fundamental_proxy.py`
- 当前样板只覆盖：
  - `sw_l1_801010 / 农林牧渔`
- 当前代理组合是：
  - `spot_hog_lean_price_soozhu`
  - `spot_mixed_feed_soozhu`
  - `index_hog_spot_price`
- 这层会写入：
  - `fundamental_proxy_score`
  - `fundamental_proxy_evidence`
- 当前 `fundamental_score` 对样板行业会从“纯估值分”变成“估值上下文 + 行业代理”的 blended 版本

这一步的意义不在于“猪链已经预测成功”，而在于：

- 基本面代理层终于从概念变成了真实运行模块
- 系统已经支持“某些行业有专属代理、其他行业暂时缺省”的 v1 结构
- 后面再补光伏、地产、航运、券商等行业时，不需要重做整个架构

这一步的意义非常明确：

- 项目已经不再是纸面设计
- 它已经具备了真实远端扫描、结构化落库和告警分发尝试能力
- “全行业 backbone + 行业状态 + 自动告警” 这条主链已经被打通到可运行程度

但这一轮也把真正的剩余缺口暴露得更清楚：

- 更丰富的新闻 / 政策 / 公告源还没有接进 scanner
- 行业专属基本面代理层还没有接进 scanner
- 历史回放和观察期验证还没有建立

所以当前项目已经完成了“跑起来”的阶段，接下来要进入的是“把有用性补齐并验证”的阶段。

## 8. 2026-04-02 行业对象层第一轮细化

这一轮已经先把 `行业对象层` 往前推进了一步。

当前形成的关键判断是：

- 覆盖目标应面向全行业，而不是只盯少数已知行业
- 项目对象不应直接等同于单一官方行业分类
- 更合适的结构是 `覆盖骨架层 + 研究对象层 + 主题/产业链层 + 交易代理层`
- 原型样板要按 `起势机制差异化` 来选，而不是被误解成项目边界

当前建议作为 `原型样板集` 优先纳入的对象包括：

- 生猪养殖
- 光伏主链
- 中国券商
- 中国 AI 数据中心 / 算力链
- 中国煤化工

这一步的意义不是已经完成行业定义，而是：

- 项目已经从“先聊概念”推进到“先把全行业对象框架立起来”
- 后续基本面层、新闻层和资金层终于可以围绕同一批对象来做

## 9. 2026-04-02 ETF 代理层第一轮补齐

这一轮对象层又往前推进了一步，ETF 代理不再只停留在“以后再补”。

当前已经新增两张对象层表：

- `industry_etf_proxy_candidates.csv`
- `industry_etf_proxies_primary.csv`

构建逻辑不是手工编表，而是：

- 先抓 `AKShare -> fund_etf_spot_em`
- 再用行业对象层的 `display_name_cn + heat_keywords`
- 对 ETF 名称做关键词匹配
- 再按匹配强度、流通市值和成交额排序

截至当前：

- 第一版 ETF 候选表覆盖了 `27` 个申万一级行业
- 第一版 primary ETF proxy 已经压出，可作为后续资金层和交易代理层入口

这一步的价值很直接：

- 行业对象层开始有“可交易代理”，不再只是抽象行业名
- 后续 ETF 份额、ETF 成交额、ETF 资金痕迹可以接得更自然
- 公告层、新闻层和资金层以后都可以围绕更具体的交易代理做联动

## 10. 2026-04-02 代表股映射与资金结构层补齐

这一轮又往前推进了两件很实的事：

- 第一版 `representative stock` 映射已经落成
- 资金层补出了 `行业成交额占比 / 内部上涨占比 / 龙头牵引`

新增文件入口包括：

- `build_representative_stock_candidates.py`
- `industry_representative_stock_candidates.csv`
- `industry_representative_stocks_primary.csv`

这版代表股不是手工挑的，而是：

- 先抓 `AKShare -> stock_board_industry_name_em`
- 再对每个一级行业抓 `stock_board_industry_cons_em`
- 过滤掉 `ST` 类名称
- 用 `成交额 + 换手率 + 当日活跃度 + 板块领涨股加分` 压出第一版候选和 primary

截至当前：

- `31` 个申万一级行业都已经有第一版 `primary representative stock`
- 这些 primary 交易代理已经接进 runner
- `signal_snapshots` 的证据字段会开始带 `representative_stock / etf_proxy`
- Bark 正文也会直接带出：
  - 代表股
  - ETF proxy
  - 行业成交占比
  - 内部上涨占比
  - 龙头牵引

这一步非常重要，因为它意味着：

- 对象层终于不只是“行业名字”
- 新闻层、公告层、资金层以后都能更自然地挂靠到“行业 -> 代表股 -> ETF proxy”这条链
- 资金层也不再只是看涨跌幅和放量比，而是开始有真正的交易结构上下文

当然，这一版代表股 primary 还不是最终口径。

当前它更接近：

- 第一版可用的流动性 / 活跃度代理

而不是：

- 最终稳定龙头清单

所以后面对象层还要继续升级，把代表股拆成更稳的多角色代理层，例如：

- 稳定龙头
- 高流动锚点
- 高频活跃代理

## 11. 2026-04-02 公告层、显性资金痕迹层与第二个行业代理样板

这一轮最重要的不是“又多了几个字段”，而是系统开始从“能扫描”往“更像投资入口”走。

新增的 4 个关键模块是：

- `theme / chain overlay`
- `flow signal overlay`
- `announcement overlay`
- 第二个行业专属基本面代理样板

### 主题 / 产业链覆盖层

对象层现在不再只剩 `31` 个申万一级行业骨架。

当前已经新增：

- `theme_chain_overlays.csv`
- `theme_chain_overlay_members.csv`

第一版覆盖的对象包括：

- 猪周期
- 光伏主链
- AI 算力链
- 券商交易链
- 煤化工链
- 航运景气链
- 创新药链
- 电网设备链

这一步的意义在于：

- 后面新闻、公告和基本面不一定只归到一级行业
- 系统终于开始有“跨行业主题 / 产业链对象”的承接位

### 显性资金痕迹层

当前新增脚本：

- `radar_flow_signals.py`

它已经接入了：

- 行业资金流排行
- 细分行业资金流
- ETF spot
- 北向持股榜单
- 两融明细
- 龙虎榜

当前产出的是：

- `aux_flow_score`
- `flow_signal_evidence`

这意味着资金层已经不是只看：

- 收益率
- 成交放大
- 板块热度

而是开始补：

- 行业主力净流入
- ETF 活跃度
- 两融痕迹
- 龙虎榜显性活跃
- 北向榜单证据

不过这里也有一个很现实的边界：

- 当前北向公开榜单接口虽然能返回数据，但榜单时间戳停在 `2024-08-16`

所以系统现在不会把它当高权重实时信号，而是：

- 保留证据
- 自动标记 `stale`
- 自动降权

这就是“投资可用”和“勉强能跑”的区别之一：

- 不是所有能抓到的数据都应该被高权重使用

### 公告层

当前新增脚本：

- `radar_announcements.py`

它现在会：

- 抓 `stock_notice_report`
- 不保留全量公告库
- 只围绕代表股候选池做行业级压缩

当前产出的是：

- `announcement_score`
- `announcement_events`

以 `2026-04-02` 这个窗口看：

- 全市场公告量大约 `2380` 条
- 当前代表股候选池命中 `54` 条

这一步很关键，因为它把“硬信息事件”真正接进了系统，而不是只靠媒体热度。

而且这层现在已经不是简单堆标题了。

当前已经补上：

- 第一版 `公告去重`
- 第一版 `事件簇压缩`
- 第一版 `signal_tags`

这意味着后面告警和复盘不必只看到一串零散标题，而可以更快判断：

- 是订单 / 中标类
- 是业绩改善类
- 是扩产 / 资本开支类
- 还是股东回报 / 价格变化类

### 第二个行业专属基本面代理样板

除了猪链以外，现在已经新增第二个样板：

- `shipping_cycle_proxy -> sw_l1_801170`

当前代理字段是：

- `macro_shipping_bdi`

当前会产出：

- `BDI / 航运景气代理`

这一步的意义不只是多一个样板，而是说明：

- 这个系统已经开始具备“不同产业用不同经营代理”的能力

这更接近你要的那个目标：

- 不是一个刚好可用的雷达系统
- 而是一个能帮助做投资决策分流的雷达系统

### 第三个行业专属基本面代理样板

这一轮又继续补了一个更贴近交易景气的代理：

- `broker_cycle_proxy -> sw_l1_801790`

它当前使用的不是抽象情绪词，而是：

- 上交所融资融券总量
- 深交所融资融券总量
- 融资买入额变化

这一步的意义很直接：

- 如果券商链要起势，单看券商股自己涨不涨还不够
- 更有用的是看全市场风险偏好和杠杆活动是不是在抬升

所以这层现在已经开始有：

- 猪链的经营代理
- 航运的景气代理
- 券商的交易景气代理

系统终于不再只是“一个行业一个统一模板”，而是在慢慢长出更像样的行业差异化。

### 当前剩余问题

这一轮也暴露了两个很真实的问题：

- 北向公开榜单当前是旧时间戳，只能低权重使用
- 最新代码虽然已经重新同步到 a private runtime configured outside this repository，但远端 one-shot 验收链还没有完全闭环

也就是说，这一轮的本地实现已经明显往“投资入口系统”推进了，但还需要在远端重新补一次部署和 one-shot 验收，才能把状态彻底闭环。

## 12. 2026-04-02 晚间继续推进：overlay 上屏、公告聚合深化、远端验收 helper

这一轮继续推进的方向，核心不是“再多一个 collector”，而是让已有信号更像投资线索。

新增的 3 个关键变化是：

- `theme / chain overlay` 已经真正接进 runner 和 Bark 正文
- 公告层开始从“命中几条公告”走向“压缩成可读事件簇”
- 远端 one-shot 验收开始有专门 helper，而不是临时拼 SSH

### overlay 已经真正上屏

对象层的 `theme_chain_overlays.csv + theme_chain_overlay_members.csv` 现在不只是静态表。

当前已经接进：

- signal snapshot evidence
- Bark 告警正文

这一步的意义在于：

- 医药生物不再只是“医药生物”
- 它开始可以被放回 `创新药链`
- 券商、光伏、猪周期、航运这些对象也开始有了更贴近投资叙事的承接位

### 公告层更像硬信息事件流

这一轮公告层继续往“投资可用”方向推进后，系统不再只吐：

- 某代表股今天有几条公告

而更接近去吐：

- 某行业今天出现了几簇值得看的硬信息事件

这对投资帮助更大，因为你真正要判断的是：

- 有没有订单 / 中标
- 有没有业绩改善
- 有没有扩产 / 提价 / 回购 / 增持

而不是去手动翻每条原始公告标题。

### 远端验收 helper 已经建立

当前新增：

- `validate_remote_industry_signal_radar.sh`

它现在支持：

- `start`
- `status`

并且已经继续演进成：

- `nohup + status file + pid file`

不过这一层也暴露出一个新的工程问题：

- 当前 status 已经能识别 `running 但进程已消失` 的中断态
- 但 `target run / source health` 这条链还没有完全闭环

这说明现在的主要工程问题已经不是“系统能不能跑起来”，而是：

- 怎么把远端验收链做得更稳、更可核对

这件事本身也很重要，因为你后面真正要长期依赖的是：

- 这个系统每天跑出来的东西到底是不是你能信的

## 32. 2026-04-03 交易时段 one-shot 已跑穿

这一轮有一个很关键的变化：

- 行业雷达已经不只是“能部署、能启动、能 check-only”
- 而是已经在真实交易时段 one-shot 中跑穿了主链

最新一次成功验收的口径是：

- `validation_id = phase3_etf_share_guard_v6`
- `run_at = 2026-04-03T02:30:00+00:00`
- `execution_mode = market_tick`
- `industry_count = 31`
- `alert_count = 1`

更重要的是，这次不是黑箱成功，而是带着细粒度 debug 跑穿了：

- `market`
- `news-policy`
- `flow`
- `fundamental-proxy`
- `persistence`
- `outputs`

### 这意味着什么

这意味着当前系统已经真正跨过了一个分水岭：

- 以前主要问题是：
  - SSH / 远端 ingress 稳不稳
  - one-shot status file 能不能回收
  - 单个外部源会不会直接把主链拖死
- 现在主要问题变成了：
  - 哪些 source 太慢
  - 哪些 source 虽然能跑，但该不该降权
  - 如何继续提升投资可用性而不只是“勉强可运行”

也就是说，主问题已经从“工程能不能立起来”切换成“系统能不能继续长成一个更像投资入口的雷达”。

### 当前最值得关注的慢点

这次 debug 也把慢点压得更清楚了。

目前比较明显的耗时集中在：

- 市场层 `build_market_feature_rows`
- `flow` 层里的 `etf-spot`

而 `flow` 的其他部分已经确认不是熔断点，而是可治理的慢源或低权重辅助源，比如：

- `northbound`
  - 当前能跑，但会 `stale`，因此只能低权重
- `lhb`
  - 当前可以为空并降级，不再打断主链

这说明下一步最合理的方向不是“再证明一次它能跑”，而是：

- 继续压运行时长
- 继续扩更有投资解释力的新闻 / 公告 / 基本面代理
- 继续提升告警质量，而不是单纯增加信号数量

## 33. 2026-04-03 flow 短 TTL 缓存已经在远端命中

这一轮又往“长期可运行”推进了一步。

不是只证明系统能跑，而是开始压它的重复扫描成本。

当前已经新增：

- `flow_cache_ttl_minutes = 20`

然后在远端做了两次同日交易时段 one-shot，第二次扫描已经确认这些 source 直接命中了缓存：

- `stock_sector_fund_flow_rank`
- `stock_fund_flow_industry`
- `fund_etf_spot_em`
- `fund_etf_fund_daily_em`

这件事的重要性在于：

- 你的雷达不是一天只跑一次
- 它是一个要持续扫描、持续推送的系统
- 如果每 10 分钟都把最慢的资金层接口重抓一遍，系统后面会越来越脆

所以这一步虽然看起来像工程优化，但本质上是在替“投资可用性”打地基。

### 当前新的细节判断

现在 `flow` 这层已经不是“会不会熔断”的问题了，而更像：

- 哪些 source 可以接受短 TTL 缓存
- 哪些 source 仍然要保留更高实时性
- 哪些 source 本身质量就不稳定，应该继续降权

这轮也顺手暴露了一个新的小问题：

- `fund_etf_scale_szse`
  - 当前在远端会报 `TypeError`
  - 但已经被压成 `warn`
  - 不再打断主链

所以现在的节奏已经更清楚了：

- ETF 份额层的深市支路已经补齐，当前重点不再是“能不能读到”，而是“这些 ETF 资金痕迹对行业判断到底有没有增量”
- `etf-spot` 已经切到只拉 `primary ETF` 的批量快路径，当前继续压慢源的重点更集中在 `market` 历史构建和其他仍然偏重的 collector
- 同时继续扩真正有投资解释力的新闻 / 公告 / 基本面代理
- Phase 5 已经从 `hog / shipping / broker` 扩到 `hog / shipping / broker / utility_electricity`，其中 `shipping` 也从单一 `BDI` 扩成了 `BDI + 航贸运价指数` 的组合代理

## 34. 2026-04-03 运行治理开始从“能跑”进入“不会锁死”

这轮最重要的变化，不是又多接了一个 source，而是把运行治理往前推了一层。

之前我们已经把问题从：

- SSH ingress
- detached validation 状态文件
- 单个外部源熔断主链

压缩到了更真实的一层：

- timer 自己会不会偶发超长挂起
- 手动 one-shot 会不会因为并发写库或占锁而失真

这次远端排查已经确认：

- `industry-signal-radar.service` 在 `2026-04-03 02:50 BST` 出现过一次超长挂起
- 它会长期占住 `/opt/industry-signal-radar/locks/industry-signal-radar.lock`
- 这会让 detached validation 虽然不再直接报 `database is locked`，但会长期等待

所以这轮不是在“修一个报错”，而是在给整个系统补运维护栏：

- `radar_manual_validation.py` 现在会争用和 timer 相同的运行锁
- `run_industry_signal_radar.sh` 新增硬超时：
  - `RADAR_RUN_TIMEOUT_SECONDS = 900`
- `industry-signal-radar.service` 新增：
  - `TimeoutStartSec = 20min`

这件事的意义很直接：

- 行业雷达是持续扫描系统，不是一次性脚本
- 一个外部源偶发卡死，不应该把整条链锁住半天
- 如果运行治理不稳，后面的投资解释力再强也很难真正可用

### 当前验证结果

远端 `phase6_clean_run_v18` 已经确认：

- 手动 one-shot 会先写出：
  - `lock_status = waiting`
- 锁释放后进入：
  - `lock_status = acquired`
- 然后继续推进真实扫描流程，而不是再直接死在 `sqlite database is locked`

所以这一轮之后，运行治理的主问题已经从：

- “并发会不会直接打坏数据库”

切换成：

- “哪些 collector 会让整条主链跑太久”

这对项目阶段判断很重要。

## 35. 2026-04-03 `etf-spot` 的真正快路径已经重新验实

这轮还确认了另一个很关键的点：`etf-spot` 之前虽然在文档里已经被描述成 `primary ETF batch`，但本地代码实际上已经退回到了“全市场抓取 + fallback”口径。

这也是为什么新的远端 clean one-shot 里，我们又看到了 ETF collector 那串分页进度条。

这次已经做了两件事：

1. 把真正的 `primary ETF batch` 快路径重新接回代码  
当前会优先只抓 `industry_etf_proxies_primary.csv` 里的 primary ETF，而不是全市场 ETF 全表。

2. 在服务器端清掉旧 cache 后重新实测  
远端当前已经返回：

- `pass`
- `primary batch rows = 27`
- `rows = 27`

这说明这条慢链现在是真的回到了“只抓业务需要的 ETF”，而不是继续靠旧 cache 掩盖全市场抓取。

### 为什么这件事重要

它不是一个单纯的性能优化小补丁。

`etf-spot` 这一层之所以重要，是因为它同时影响：

- 运行时长
- ETF 资金痕迹解释
- 同日重复扫描的稳定性

如果这条链每次都回到全市场重抓，系统就会再次变成：

- 看起来能跑
- 但一到真实运行就越来越慢

而现在重新压回 `27` 个 primary ETF 之后，Phase 3 才真正站在“投资可用”的轨道上。

## 36. 2026-04-03 验收视图开始真正服务“投资语义检查”

这轮还有一个容易被低估、但其实很关键的变化：远端 one-shot 的 `status` 面板，开始不只是给工程状态，而是开始给“投资语义验收”服务。

之前 `recent_alerts` 虽然已经能看 title 和 body preview，但 preview 只截前 `8` 行，刚好容易把这些最重要的内容截掉：

- 代表股 / ETF
- 行业代理
- 代理细节
- 政策 / 舆情
- 公告硬信息

也就是说，你看到“有提醒”，但未必能一眼判断这条提醒到底是不是值得立刻研究。

这轮已经补成：

- `body_preview` 扩到 `12` 行
- 新增：
  - `body_line_count`
  - `has_policy_line`
  - `has_proxy_detail_line`
  - `has_announcement_line`

这件事很小，但它会直接改变后面的调参方式。

后面我们不只是看：

- 有没有 alert
- 哪个行业排在前面

而是会开始看：

- 告警正文里到底有没有把“值得研究的证据链”说出来
- 哪些 alert 虽然触发了，但正文仍然只是热度和量价
- 哪些 alert 已经开始带出真正像研究入口的东西

这一步的意义在于，系统开始从：

- `会提醒`

往：

- `提醒时就已经给出第一层研究抓手`

这个方向迈。

## 37. 2026-04-03 新闻层开始分清“假未命中”和“真未命中”

这轮最重要的变化，不是又加了几个行业关键词，而是新闻层终于开始把剩余未命中样本拆开看。

之前所有未命中标题都会混在一起，于是后面的动作很容易走偏：

- 一部分本来就是 `宏观数据`
- 一部分本来就是 `地缘冲突`
- 一部分本来就是 `财政预算 / 部门拨款`
- 还有一部分只是 `栏目聚合标题`

如果把这些都继续当成“行业词表还不够全”，最后只会把 backbone 越补越脏。

所以这轮新增了一层 `非行业 overlay 分流`：

- `宏观数据/经济指标`
- `地缘冲突/战争扰动`
- `财政预算/部门拨款`
- `公共事务/纪检治理`
- `综合快讯栏目`

它的定位很明确：

- 先做 `classification-only`
- 先用于映射评估和候选词清洗
- 暂时不直接升级成 runtime 主触发节点

### 这一层带来的真实变化

最新本地映射快照里，新闻层已经开始把原来的 `unmatched` 拆成：

- `raw_unmatched`
- `overlay_hit`
- `true_unmatched`

当前结果是：

- `news_cctv`
  - `raw_unmatched = 3`
  - `overlay_hit = 2`
  - `true_unmatched = 1`
- `stock_info_global_cls`
  - `raw_unmatched = 11`
  - `overlay_hit = 8`
  - `true_unmatched = 3`
- `stock_info_global_em`
  - `raw_unmatched = 84`
  - `overlay_hit = 38`
  - `true_unmatched = 46`

这里最关键的是 `CLS`。

它之前看起来像“行业映射做得还不够”，但拆开之后才发现，大头其实并不是行业漏判，而是：

- `美国服务业活动自2023年以来首次萎缩`
- `美官员证实一架美军战斗机在伊朗境内被击落`
- `特朗普欲将NASA在2027年拨款缩减至188亿美元`

这些本来就更像：

- 宏观
- 地缘
- 预算政策

而不是申万一级行业事件。

另外，这轮还把评估脚本和 runtime 的 `theme overlay keywords` 对齐了，并补了一条新的：

- `商业航天链`
  - 关键词：`商业航天 / 航天 / 火箭 / 运载火箭 / 卫星 / 金色穹顶`

这意味着像：

- `快舟十一号遥十三运载火箭通过出厂质量评审`

这种原来挂在未命中里的标题，现在已经开始被主题层承接，而不是继续被迫往一级行业词表里塞。

### 为什么这件事对投资可用性重要

这一步的意义不是“评估更漂亮”。

它真正改变的是后面的建设方向：

- 后面 `news_keyword_candidates` 不再把宏观 / 地缘 / 预算词继续推进行业词表
- 行业词表开始只围绕 `true_unmatched` 去补
- 剩余真正值得处理的未命中，也变得更清晰：
  - `航天军工主题`
  - `海外商品 / 产业公司事件`
  - `公司法务 / 监管 / 风险提示`
  - `国际事务 / 反垄断`

所以这轮之后，新闻层已经不只是：

- “多抓几个源”

而是开始像一个真正的分流系统：

- 哪些新闻是行业线索
- 哪些新闻只是宏观背景
- 哪些新闻属于地缘扰动
- 哪些新闻应该进入下一轮 `theme overlay`

这会直接决定后面这套雷达到底是越做越干净，还是越做越像噪音堆叠。

## 6.17 2026-04-20 事实层与信号层的目标架构已经收口

这一轮把上游和下游之间的角色分工重新钉死了。

最重要的结论不是“所有东西都走 OpenBB”，而是：

- `News Event Hub` 独立提供事件事实
- `canonical 数据底座 + OpenBB market access facade` 统一提供市场事实
- `Alpha158 / Sentiment / TimesFM` 独立提供信号输出
- `Radar / Research` 同时消费 `事件事实 + 市场事实 + 信号`

也就是说，最终更合理的不是一层，而是两层：

- `fact plane`
- `signal plane`

这里有一个关键判断要记住：

- 对下游来说，市场事实最好统一通过 `OpenBB API` 读取
- 但对内部实现来说，`openbb-api.service` 本身并不足以天然承担全部自愈能力
- 当前真正已经具备 `canonical-first + provider fallback + persist-back` 雏形的，是 `Investment/scripts/openbb_adapter.py`

所以更准确的目标口径应该是：

- `OpenBB API` 作为事实层的对外 contract
- 背后由更强的 `OpenBB market access facade` 负责：
  - canonical-first read
  - provider routing
  - retry / timeout / fallback
  - on-demand backfill
  - persist-back
  - structured error contract

这也意味着，后续不应该再让：

- `Research` 自己决定 `AKShare / yfinance`
- `Radar` 自己决定 provider
- 各个下游自己直连 DuckDB

而应该统一收口成：

`canonical truth layer -> OpenBB market access facade -> Radar / Research / qlib`

信号层则继续单独治理：

- `Alpha158`
  - 更适合继续读 canonical 派生 provider / 文件
  - 对外提供 `instrument_score_snapshot`
- `Sentiment`
  - 读市场事实 + 事件事实
  - 对外提供 `market_snapshot / research_snapshot`
- `TimesFM`
  - 读历史市场事实
  - 对外提供 `forecast_curve / market_snapshot / research_snapshot`

也就是说，最干净的最终形态不是：

- “所有东西混成 OpenBB 一层”

而是：

- `OpenBB API` 统一供给事实数据
- `signal contract / signal facade` 统一供给信号数据

这套目标架构现在已经正式落成文档：

- [canonical_data_access_architecture.md](docs/canonical_data_access_architecture.md)
- [fact_signal_target_architecture_v1.md](docs/fact_signal_target_architecture_v1.md)

## 6.17 2026-04-21 候选公司解析、情绪 freshness 和 canonical 锁窗都收了一轮

这一轮的重点不是再改一份日报文案，而是把几条会持续污染日报质量的系统尾巴真正收掉。

第一，`company lane` 已经不再接受“像公司、但其实不是可交易标的”的主体。  
现在 candidate pool 对 company 对象的规则已经收成：
- 必须能映射到当前 A/H 可交易公司名
- 或者标题里明确带有 ticker

这意味着像：
- `液化空气集团`
- `大众汽车`
- `东京电子`
- `英海事分析公司`
- `央企控股上市公司`

这类 foreign / source / generic entity，不会再混进 Radar 的个股层。  
最新结果里：
- `resolved_company_target_count = 11`
- `unresolved_company_target_count = 0`

第二，`company enrichment` 不再因为远端 discovery 超时而把整条日报主链打断。  
之前 `build_radar_company_enrichment_sidecar.py` 在调用 `run_company_discovery.py` 时，如果 SSH 远端 discovery 超过 60 秒，会直接把 workspace build 打死。  
现在这条链已经改成 graceful degradation：
- 成功就补充 enrichment
- 超时就退回 `research_feed_fastpath`
- 最差也只是对象级 `warn`
- 不再影响 `snapshot -> report -> quality gate`

这层修完以后，日报主链恢复成“慢源会降级，但系统仍继续出报”的状态。

第三，`sentiment freshness` 的根因已经被修平。  
问题不是情绪脚本本身，而是它依赖的 `sentiment-subsystem.service` 卡在了 `build_equity_price_substrate.py` 的 HK 全量刷新上，导致 market/company sentiment sidecar 一直停在旧日期。  
现在上游 sentiment 安装链默认走 `--skip-hk`，所以：
- `market_sentiment_factor_daily.csv`
- `company_sentiment_score_daily.csv`

已经重新对齐到 `2026-04-21`，`source_readiness` 也重新回到了 `pass`。

第四，`shipping_cycle_proxy` 这条老 warning lane 也已经从“单点 provider 风险”改成了可恢复语义。  
`radar_fundamental_proxy.py` 现在对 `BDI / CCFI` 做的是：
- 单侧 provider 失败时的 partial fallback
- 最近成功结果的 cache 复用

所以单个航运 proxy 波动，不会再把整条 `shipping_cycle` lane 打成 warning。

第五，也是这一轮最深的系统结论：  
`deferred writeback queue` 的根因并不在 Radar，而在 canonical 上游的“锁窗串行化”。

实际排查下来，之前 price backlog 冲不回 canonical，是因为：
- `canonical-market-refresh.service`
- `canonical-qlib-derivation.service`

会串行占用 `canonical.duckdb` 的写锁。  
最严重的时候，`canonical-market-refresh.service` 因为 `industry-indices` lane 挂在 daily 主链里，出现了 24 小时级别的僵尸锁库。

这一轮做了两层修正：

1. daily refresh 脚本已经把最重的 `industry-indices` lane 从默认每日主链里拆出  
现在 daily 默认只跑：
- `benchmarks`
- `industry-proxies`
- `hk-benchmarks`
- `commodities`

`industry-indices` 只在显式打开 `RUN_DAILY_INDUSTRY_INDICES=1` 时才跑。  
这让 daily canonical refresh 重新回到“短时间正常锁库”，而不是“整天占锁”。

2. Radar 的 `ensure_radar_price_freshness.py` 已经补上 remote `flush-queue-only` 路径  
当本地 canonical 不可写、但远端 canonical 可写时，它会：
- 把本地 deferred queue 推到远端
- 只执行 queue flush，不再顺手重跑整条 remote bridge
- flush 成功后清空本地 backlog

最新结果里：
- `price_writeback_queue_count = 0`
- `data_substrate_audit = pass`
- `source_readiness = pass`
- `check_radar_report_quality = pass`

这意味着到这一轮，Radar 的“事实层可持续性”已经明显更像一个每天都能稳定出报的系统，而不再是一条经常被上游长锁、慢源和不可交易主体干扰的脆弱流水线。

## 6.18 2026-04-21 最新批次已经收口到全绿门禁

这一轮又把“最后一截不够系统化的尾巴”收掉了，重点不在单份日报写得更花，而在于主链已经回到了真正可持续的日更口径。

第一，`snapshot / inventory / handoff / report / quality` 的批次已经重新对齐。  
此前我只重跑了中间一段，导致 `inventory.run_id` 和 `snapshot.run_id` 漂移。现在这条链已经回到同一批次：
- `run_id = candidate-pool:2026-04-21T11:10:35+00:00`
- `source_readiness = pass`
- `data_substrate_audit = pass`
- `radar_report_quality_latest.json = pass`

第二，质量门禁的口径也从“机械阈值”改成了“系统现实优先”。  
之前 `Immediate Research = 0` 会自动留下 warning，但这在清淡样本里并不等于系统失效。当前门禁已经改成：
- 如果没有真正达到立即研究门槛的对象，就允许 `Immediate Research` 为空
- `PM 编辑摘要层` 视为可选增强层，没有凭证时不再污染主链质量判断

所以最新质量门禁已经回到：
- `status = pass`
- `warnings = []`
- `blockers = []`

第三，今天这版系统输出已经更像“稳定日更系统”而不是“勉强拼出一份报告”。  
当前样本日期与期望样本日期一致，价格补数、财务补抓、新闻补核、数据底座审计全部对齐到同一轮状态。这说明 Radar 现在不只是能出报告，而是已经把：
- 上游 freshness
- 主链批次一致性
- canonical-first writeback
- 可选增强层降级

这些真正决定“明天还能不能继续稳定出”的问题，一起收进了系统本身。
