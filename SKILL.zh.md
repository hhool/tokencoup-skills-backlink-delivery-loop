---
name: backlink-delivery-loop
description: 用于为某个中心域名准备或复核外链投递闭环：消费 tokencoup-skills-backlink-prospector 导出的带版本候选快照、把运维散文结构化为联系地址、计算投递节奏与 submitted→live 观察窗、产出以 link-ledger 为事实依据的仅供审阅清单。本 skill 不发邮件、不提交表单。
agent_created: true
---

# 外链投递闭环

## 用途

把某个中心域名的候选外链池，变成**可审阅的投递清单**与完整证据链，再观察外链是否真正上线。本 skill 是**独立分发包**：不会被 WorkBuddy 的 skill 扫描器自动加载，必须显式安装。

**硬边界**：本包不联网、不提交表单、不发送邮件。`BDL_AUTO_TRANSMIT=true` 会在启动时被拒绝。发送动作保留在人工门之后（R-520；R-530 中 U 类目标的 tier B）。

## 不变量

| 术语 | 定义 |
|---|---|
| `center_domain` | 我们自己的中心域名，例如 `soundbuttons.cc`；由 site slug 解析，不写进封闭的台账行 |
| `target_domain` | 可能承载外链的第三方域名 |
| 主键 | `(center_domain, target_domain, channel_type, target_url)` |
| 事实源 | `sites/<slug>/07-links/link-ledger.json`，契约 `link-ledger` |
| 终局成功 | `state: live` 且 `verify.outcome: found`（L4）；不存在「投递成功」终态 |

上游快照的 `url` 是**站点入口**，不是 `link-ledger.target_url`。绝不能把它提升为落点页：那会让每次 L4 抓取都抓错页面，产生系统性假阴性。

## 工作流

1. `doctor` —— 校验配置，确认发送闸门关闭。
2. `validate-snapshot --file <export>` —— 校验 schema 版本、哈希与中心域名。
3. `build-manifest --snapshot <export> --history <send-history.json>` —— 读取快照、join 散文联系地址、应用准入与节奏规则，写出 `delivery-manifest.json` 与 append-only 的 `receipts.jsonl`。
4. 人工逐条审阅 `ready_for_review` 行；任何工具都不得自动执行。
5. `plan-window --submitted-at <iso>` —— 计算 T+1/3/7/14/30 天抓取排期。
6. `classify --outcome ... --http-status ...` —— 记录一次观测。403、超时、robots 拒绝记 `unknown`，绝不记 `absent`。

## 状态

`ready_for_review`（材料齐、待人工门）—— `needs_enrichment`（缺落点页或投递通道，写入 `gaps`）—— `deferred`（90 天重复窗）—— `blocked`（竞品、已拥有、互链、不可达、地址被排除、已有 live 链接）。

## 分层

- 入口层 `bdl/cli.py`：只解析参数、格式化 JSON。
- 服务层 `normalize`、`cadence`、`window`、`pipeline`：承载业务规则，不碰 HTTP 与文件句柄。
- 适配层 `snapshot`、`prose`、`outbox`：承担全部 I/O。
- 横切：集中环境变量校验、类型化错误 + 全局处理、带 `batch_id` 的结构化 JSON 日志、边界输入校验、幂等键、原子写与 append-only 回执。

## References

| 何时加载 | 文件 |
|---|---|
| 字段映射、状态机、L1–L4、观察窗 | `references/state-and-evidence.md` |
| R-520 / R-530 裁决与改规则成本 | `references/rule-boundary.md` |
| 散文抽取、排除、溯源与漂移 | `references/prose-extraction.md` |
| 上游快照契约 C 与类型映射 | `references/upstream-contract.md` |

中文镜像为 `SKILL.zh.md` 与 `references/*.zh.md`。英文为主文档；代码与英文文档不含汉字。
