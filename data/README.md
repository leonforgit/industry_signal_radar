# 数据目录说明

这个目录用于保存：

- 小型映射表
- schema
- 配置样例
- 行业标签表
- fixture
- source manifest
- 全行业骨架 registry
- 第一版 `31` 个申万一级行业 backbone
- 第一版行业基本面代理定义

这个目录当前不用于保存：

- 大批量新闻原文
- 抓取 HTML 缓存
- 数据库副本
- 高频运行中间产物

如果未来出现高体量数据层，默认放在 repo 外部，只把必要的结构说明和样例保留在这里。

当前新增的小型结构化文件包括：

- `radar_opportunity_snapshot_fixture_v1.json`
- `radar_candidate_pool_fixture_v1.json`
- `radar_contract_validation_cases_v1.json`
- `industry_registry_sw_level1.csv`
- `fundamental_proxy_definitions.json`
- `industry_etf_proxy_candidates.csv`
- `industry_etf_proxies_primary.csv`
- `industry_representative_stock_candidates.csv`
- `industry_representative_stocks_primary.csv`
- `theme_chain_overlays.csv`
- `theme_chain_overlay_members.csv`
- `non_industry_news_overlays.csv`
- `company_mapping_overrides.json`
