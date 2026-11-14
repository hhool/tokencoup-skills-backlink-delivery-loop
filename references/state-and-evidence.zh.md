# 状态机、证据阶梯与观察窗

> 实现状态：排期与判定在 `bdl/services/window.py`；页面抓取在 `bdl/services/scan.py`（注入 `Fetcher`）；L2 回执轮询在 `bdl/services/receipts.py`。英文主文档为 `state-and-evidence.md`。

## 1. 四层证据阶梯

| 层 | 可观测证据 | 本地可判定 | 写入位置 |
|---|---|---|---|
| L1 提交动作 | 表单 POST 的 2xx + 感谢页 URL + 截图；邮件 `accepted` + `provider_id` | 是 | `submitted_at`、`confirmation_ref`、evidence |
| L2 传输回执 | `GET /emails/{id}` → `delivered` / `bounced` / `complained` | 是 | `transport-receipts.jsonl` + 事件日志 |
| L3 站点受理 | 对方后台是否收到、编辑是否审核 | **否** | 不写；`gaps` 记原因 |
| L4 链接上线 | `target_url` HTML 中出现指向 `own_url` 的 `<a>` | 是（长时窗） | `live_at`、`verify` 快照 |

`delivered` 只证明对方 MX 接收，不证明有人阅读；不得使用「保证送达」表述（R-530）。

## 2. 事实源字段映射（link-ledger.schema.json）

| 用途 | 字段 | 备注 |
|---|---|---|
| 目标页 | `target_url` | 链接最终出现的第三方页面 |
| 落点 | `own_url` | 被指向的自家页面 |
| 表单地址 | `submission_url` | 提交按钮页或 form action，**区别于 target_url** |
| 状态 | `state` | `registered / submitted / approved / live / indexed / lost / rejected / error` |
| 跃迁 | `created_at`、`prepared_at`、`registered_at`、`submitted_at`、`approved_at`、`live_at`、`indexed_at`、`lost_at` | 每次跃迁都要时间戳 |
| 回执 | `confirmation_ref` | 指向磁盘上的确认页/邮件路径或回执 id；不得伪造（R-414） |
| 最新标量 | `http_status`、`link_present`、`rel`、`anchor_text`、`last_checked_at`、`next_check_due` | 与 `verify` 快照互补 |
| 核验快照 | `verify{outcome, content_hash, http_status, observed_href, host_linked, host_href, rel, fetched_at, tool}` | `outcome: found / absent / unknown` |
| 基线 | `confirmed_rel` | 确认 live 那一刻的 rel，只写一次，永不改写 |
| 兜底 | `notes`、`gaps[]`、`scores.gaps` | `provider_id`、批次号、观察窗结论写 `notes`；缺失数据写 `gaps` |

契约 `additionalProperties: false`，`verify` 也是封闭对象。新增字段会令 `validate` 变红。

## 3. 双层状态，单向映射

```
backlink-prospector 快照（只读候选）
      ↓ 富化 + 人工确认
OUTREACH-QUEUE.status  pending / in_progress / submitted / failed / skipped / verified(仅人)
      ↓ 人确认 + 契约校验
link-ledger.state      registered → submitted → approved → live → indexed → lost
```

- `verified` 被 `record_result.py` 硬拒；链接是否存在是测量结果，不是提交结果。
- 执行端写 `status`，事实端 `state` 只能由人依据实测推进（`AGENT-PLAYBOOK.md` 第一节第三条）。

## 4. 观察窗（本设计新增，需规则承载）

用户裁决：终局成功必须是 L4。由此产生的两个约束：

1. **短时窗不能作为成功判据。** 编辑审核通常数天到数周；实测记录中某目录免费档自述评审 30–60 天。短窗内 `absent` 是常态。
2. **R-512 / R-513 当前不给 `submitted` 行抓取预算**：R-512 明确「只有 `live` / `indexed` 行进双周检查集，`registered` / `submitted` 不消耗检查预算」，R-513 也只抓 `live` / `indexed`。若不新增规则，L4 判据无法落地。

已采用解法（方案 A，零契约改动）：

- 新增一条「待生效观察窗」规则，规定 `submitted` / `approved` 行在 `T+1/3/7/14/30 d` 各抓一次 `target_url`，最多 6 次（沿用 `LINK_RECHECK_LIMIT = 6`），带 UA、`--delay`、遵守 robots。
- 观测结果写入 `outreach-receipts.json`（derived report），不动 `link-ledger.state`。
- 复用已存在的 `next_check_due` / `last_checked_at` 字段排期，不新增字段。
- 命中 `found` 后由人执行 `linkloop.confirm` 推进 `live`。
- 该规则需注册执行脚本到 `tools/registry.json`（按 `sextant-rule-and-tool` 的顺序）。

不推荐解法（方案 B）：给 `state` 枚举加 `observing`。这属于契约 minor bump，需要 `VERSION-MATRIX.yaml`、12 个 `contracts.lock`、各 SKILL frontmatter、CHANGELOG、`test_contracts.py::PUBLISHED`、docs 全套同步；且 `sextant-rule-and-tool` 明确警告「未定型的 artifact 形状不要冻结成 schema」「不要冻结没有生产者写入的字段」。

## 5. 时间窗口径

| 窗口 | 动作 | 允许结论 |
|---|---|---|
| `T+0~2 min` | 记录传输层回执 | `submitted` / `failed` |
| `T+5 min~24 h` | 指数退避轮询 provider 事件 | `delivered` / `bounced` / `complained` |
| `T+1/3/7/14/30 d` | 只读抓取 `target_url` | `found` / `absent` / `unknown` |
| 窗口结束 | 转长期复查，写 `next_check_due` | 保持 `submitted`，**不判失败、不重投** |

判定纪律：

- `403`、超时、robots 拒绝、JS 渲染后注入 → `unknown`，不是 `absent`（`receipts.py` 已是该口径）。
- 单次 `absent` 不判 `lost`（R-513）。
- `approved`（编辑回复/目录通过）≠ `live`（R-512）；`live` 必须由抓取页面确认。
- 页面 URL 归一化按 host + path 比较（`observed_href` 可能是相对路径或带 utm），否则产生假阴性。
- `verify.content_hash` 只在 `found` 时钉死，用于区分真实删除与页面重建。

## 6. 传输回执轮询

`record --action sent` 硬拒没有 `provider_id` 的发送，因为无法指名的信无法对账。`poll-receipts` 是另一半：每封未结案的信一次带鉴权的 GET，映射进台账词汇。

| 问题 | 代码里的答案 |
|---|---|
| 查哪些发送？ | `sent`、带 `provider_id`、在 `BDL_RECEIPT_WINDOW_DAYS` 内、且尚无定论回执 |
| 什么算定论？ | 该 `(provider_id, action)` 已有 `delivered` 或 `bounced` 事件 |
| 怎么防重复？ | 键是 `(provider_id, action)`，不用发信商时间戳——它不够精确，当幂等键会重写窗口锚点 |
| 永不写入什么？ | 由读失败推出的 `bounced`；以及任何 `live` |
| 结果落在哪？ | `transport-receipts.jsonl`（每封被查询的信，含无结论的）与（确认后的）事件日志 |

映射纪律：

- `delivered` / `opened` / `clicked` → `delivered`。打开不可能先于送达，把它当弱证据反而低估了发信商观测到的事实。
- `bounced` / `complained` / `hardbounce` / `spamcomplaint` → `bounced`，把**地址**放进 `BDL_CONTACT_LOST_DAYS` 冷却；域名的另一条渠道仍开着。
- `delivery_delayed` / `queued` / `softbounce` → `pending`，不落事件。仍在途中不是结论。
- 401、403、404、429、5xx、超时、非 JSON 响应体、无法识别的状态词 → `unknown`，不落事件。这与 L4 观测器是同一种不对称：拒绝不等于缺席。因为 API 返回 429 就把一个活邮箱关 180 天，是这种错误的昂贵版本。

一个很容易踩的传输细节：发信商 API 前面挂着 CDN，默认的 urllib UA 会被判为 Cloudflare 1010 直接拒绝。读取器显式带 `User-Agent`；不带的话每次轮询都返回 `unknown`，看起来像发信商故障，实际是客户端被拦。

回执只证明传输（L2），到此为止：不证明链接存在，不碰 `link-ledger.state`，也不构成重发许可。
