# 新闻源重叠评估快照

- 生成时间：`2026-04-03T14:34:29+00:00`
- 评估时间：`2026-04-03T14:34:22+00:00`

## 源概览

| source_id | usable_rows | unique_titles | date_cov | time_cov | note |
| --- | ---: | ---: | ---: | ---: | --- |
| `akshare:news_cctv` | 13 | 13 | 1.00 | 1.00 | used_date=20260403 |
| `akshare:stock_info_global_cls` | 16 | 16 | 1.00 | 1.00 | used_date=2026-04-03 |
| `akshare:stock_info_global_em` | 200 | 200 | 1.00 | 1.00 | used_date=2026-04-03 |

## 两两重叠

| left | right | exact_overlap | approx_overlap |
| --- | --- | ---: | ---: |
| `akshare:news_cctv` | `akshare:stock_info_global_cls` | 0 | 0 |
| `akshare:news_cctv` | `akshare:stock_info_global_em` | 0 | 0 |
| `akshare:stock_info_global_cls` | `akshare:stock_info_global_em` | 12 | 12 |

## 各源独有标题样本

### `akshare:news_cctv`
- 【新思想引领新征程】多业态融合发展 激活文旅产业新动能
- 【树立和践行正确政绩观】坚持求真务实 推动学习教育见行见效
- 【“十五五”新图景】数智化变革中的万亿新空间
- 前2个月我国中小企业生产经营持续改善
- 各地春耕生产持续推进
- 马兴瑞涉嫌严重违纪违法正接受中央纪委国家监委纪律审查和监察调查
- 清明假期明天开始 交通部门多措施保出行
- 清明假期总台推出特别节目

### `akshare:stock_info_global_cls`
- 事关数据产权登记 国家数据局公开征求意见
- 财联社4月3日晚间新闻精选
- 诺和诺德称Wegovy片剂平均减重效果显著优于礼来的orforglipron
- 特朗普欲将NASA在2027年拨款缩减至188亿美元 降幅达23%

### `akshare:stock_info_global_em`
- 特朗普欲将美国卫生与公众服务部在2027年自由支配预算缩减至1111亿美元 降幅达12.5%
- 特朗普请求为美国国家航空航天局在2027年拨款188亿美元
- 将被实施其他风险警示 山东章鼓4月7日停牌一天
- 特朗普在2027财年预算中寻求为政府机构拨款2.2万亿美元
- 伊朗战事消耗库存 美国或延迟交讫日本订购“战斧”导弹
- 美战机在伊朗坠毁美军展开搜救
- 意大利总理梅洛尼将飞往沙特阿拉伯 并还将访问卡塔尔和阿联酋
- 土耳其对谷歌发起反垄断调查
