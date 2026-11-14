# 散文 → 结构化联系地址

> 实现状态：已落在 `bdl/adapters/prose.py`，离线 fixture 为 `assets/prose/TODO-example.md`。英文主文档为 `prose-extraction.md`。

## 1. 为什么需要单独一层

`link-ledger` 没有 `contact_email` 字段，且是封闭 schema。现有联系地址散在散文里，例如：

- `sites/soundbuttons-cc/07-links/TODO-backlink.md` 的 B-1 邮件轨表格：`contact@beebom.com`、`support@airmore.ai`
- 同表另一行写的是「联系表单/邮箱」——**不是地址，是占位描述**
- 同文件 E 节「不做」明确排除「只有客服 `support@` 的三家（imyfone / easeus / dubbingai）」
- `drafts-2026-11-01.md` 有「入口：`https://assethoard.com/contact` 表单（无公开邮箱）」
- 历史文档里还有 `contact@voxbooster.com` 等，可能已落在 90 天冻结窗内

因此正则抓取邮箱会同时抓到：有效地址、占位描述、被显式排除的地址、已冻结的地址。**抽取必须带上下文语义，且结果必须人工确认。**

## 2. 抽取流水线（已实现：`bdl/adapters/prose.py`）

1. **定位来源**：登记允许扫描的散文文件白名单（`TODO-backlink.md`、`drafts-*.md`、`discovery-*.md`、`prospects*.txt`）；`--dir` 式扫描必须跳过自身输出，否则第二轮读到自己的产物。
2. **抽候选**：匹配反引号包裹的邮箱、裸邮箱、Markdown 表格行中的地址；同时记录所在章节标题、表格行全文、行号。
3. **排除节识别**：命中「E · 不做」「冻结」「不投」「别再回头讨论」「只有客服」等语义的行及其上下文，标为 `excluded` 并保留排除理由，不做静默丢弃。
4. **歧义标记**：`联系表单/邮箱`、`无公开邮箱`、`需注册`、多个地址同域、同域多入口（EN + 繁中并一封）→ 标 `ambiguous`，交人合并或判定。
5. **溯源**：每条候选写 `source_file`、`line`、`excerpt`、`content_hash`（源文件 sha256）、`extracted_at`、`rule_version`。源文件内容变化即 hash 失配 → 该条回到 `unconfirmed`，禁止自动沿用。
6. **人工确认门**：只有 `confirmed` 的地址可进入发送队列。确认动作记录确认人与时间。
7. **校验**：L1 语法、L2 域名 + MX；未检出的记为 `gap`（字段名 + 原因 + 兜底），不写 0 / false / failure。L3 `provider_id`、L4 `last_event` 由发送后观测补齐（R-530）。
8. **去重与额度**：按 `target_domain` 分组，与 `link-ledger`、`outreach-receipts.json` 对照；已有 `live` 行、90 天窗内、已 `submitted` 的均不重入队。

## 3. 派生清单的记录形状（建议，非契约）

```
address_id, center_domain, target_domain, address,
source_file, line, excerpt, content_hash, extracted_at,
context_state   (candidate | excluded | ambiguous | confirmed),
exclusion_reason, confirmed_by, confirmed_at,
level           (L1 | L2 | L3 | L4 | gap),
mx_checked_at, gap_reason
```

当前实现产出的字段为 `target_domain / address / form_url / target_url_hint / own_url_hint / state / reason / source_file / line / excerpt / content_hash`。该清单是**派生报告**，不写入 `link-ledger`；`provider_id` 等运行时信息最终以 `notes` 形式落台账。

## 4. 边界

- 散文不是事实源，`link-ledger` 才是；两者冲突时以台账为准（`TODO-backlink.md` 开头已如此声明）。
- 抽取器只做只读解析，不联网、不发送、不注册（R-520 / R-510）。
- 「提交后收到邮件要求确认 → 不要点确认链接，交给人」（`AGENT-PLAYBOOK.md` 第五节）：确认邮件同样不自动处理。
- 一个批次只碰一个渠道（R-520）；结构化产物按 `center_domain` 分片，避免跨中心域名污染（`backlink-prospector` 曾发生共享池串域）。
- 排除节、冻结项、备注中的中文说明只作为本地数据保留，不得进入外发正文（R-522 CJK 禁令）。
