# Kimi Worker Playbook

## 目标

这份文档定义 `Kimi Code` 在 a private runtime configured outside this repository 上更适合承担的“脏活累活”，以及主控代理如何做 review 和验收。

当前项目的原则不是把主控权交给 Kimi，而是：

- Kimi 负责重体力执行
- 主控代理负责任务拆分
- 主控代理负责 review / 验收 / 回写

## Kimi Code 是什么

根据官方文档，`Kimi Code CLI` 是 Moonshot AI 的终端 coding agent，支持：

- 读写代码
- 执行 shell 命令
- 搜索和抓取网页
- 自主规划多步任务

官方入口：

- `kimi --version`
- `kimi`
- 首次配置可用 `/login`

## 当前最适合外包给 Kimi 的任务

### 1. 重型远端排障

适合外包：

- 远端 detached validation 为什么中断
- 某个 collector 在 remote runtime 上是否触发解释器退出
- 某个 AKShare 接口在远端网络环境下的兼容性排查

原因：

- 这类任务以反复试命令、抓日志、做小修补为主
- 成本高，节奏碎
- 很适合让 Kimi 在 remote runtime 本地做长时间探索

主控验收重点：

- 是否定位到明确中断点
- 是否产出最小修复
- 是否给出可复现命令
- 是否没有破坏现有 runtime 边界

### 2. 扩行业代理样板

适合外包：

- 再补 1 到 3 个行业专属代理
- 例如电网设备、创新药、煤化工、光伏链

原因：

- 这类任务通常是
  - 查 AKShare 可用接口
  - 试字段
  - 做标准化
  - 接入 `fundamental_proxy` 层

主控验收重点：

- 代理变量是否真的贴合行业机制
- 是否不是生搬硬套统一模板
- evidence 字段是否可读
- source health 是否完整

### 3. 扩公告 / 新闻压缩层

适合外包：

- 公告去重优化
- signal_tags 丰富化
- 新公告源接入前的字段摸底
- 更多新闻源 parser 的第一版草稿

主控验收重点：

- 是否真的降低噪音
- 是否把文章堆砌压成事件簇
- 是否保留行业归属和证据链

### 4. 大量机械性映射工作

适合外包：

- 补主题 / 产业链 overlay membership
- 补代表股多角色候选
- 补 ETF proxy 备选层

主控验收重点：

- 是否有明显错配
- 是否有过度重复
- 是否遵守当前 `industry_id` 口径

## 当前不建议直接外包给 Kimi 的任务

### 1. 最终权重和告警口径定稿

这一步更像投资判断，不只是编码实现。

### 2. 最终成功标准判断

这关系到“它是否真能帮助投资”，必须由主控代理结合观察期结果判断。

### 3. 主报告的最终结论回写

Kimi 可以产草稿，但最终 narrative、取舍和升级/降级判断由主控代理负责。

## 推荐派单格式

在 a private runtime configured outside this repository 的项目目录下启动 `kimi` 后，优先给它这种结构化任务：

1. 目标
2. 边界
3. 允许修改的文件
4. 必须保留的行为
5. 验收命令
6. 输出格式

## 推荐的第一批外包任务

### Task A

标题：

- 修 detached validation 中断根因

任务说明：

- 在 `industry_signal_radar` 项目里排查为什么 `manual validation` 会停在 `50%` 左右后进程消失。
- 不要改 Bark。
- 不要改共享 market data 边界。
- 优先定位中断发生在：
  - news policy
  - announcement
  - flow overlay
  - fundamental proxy
  - sqlite commit
- 最终输出：
  - 根因判断
  - 最小修复 patch
  - 验证命令

### Task B

标题：

- 补光伏链或电网设备链代理样板

任务说明：

- 在 `fundamental_proxy` 层新增一个贴合行业机制的样板。
- 优先考虑：
  - 光伏链
  - 电网设备链
- 要求：
  - 有真实 AKShare 可拉取源
  - 有 source health
  - 有 summary_cn
  - 有可解释 evidence

### Task C

标题：

- 提升公告层事件簇质量

任务说明：

- 优化 `radar_announcements.py`
- 目标不是多抓标题，而是更好压缩成“可读事件簇”
- 最终输出：
  - 改动点
  - 降噪逻辑
  - 风险点

## 主控代理 review 清单

- 代码是否遵守当前 runtime 边界
- 是否引入新的大文件或运行时污染
- 是否把 source_id / source_health 贯穿完整
- 是否真的降低噪音或提升投资解释力
- 是否有最小可复现验证
- 是否需要同步更新：
  - `execution_checklist.md`
  - `STATUS.md`
  - `reports/main.md`
  - `workpapers/server_runtime.md`

## 备注

当前建议把 Kimi 当成：

- 远端 worker
- 机械执行者
- 第一轮实验员

而不是：

- 最终投资判断者
- 最终验收者
