# 上游契约 C：候选快照

## 1. 决策

候选目标来自 `tokencoup-skills-backlink-prospector`，对接方式采用**契约 C**：上游导出带版本的快照，本包只读消费。不直接读上游 `store` 文件、不共享数据库、不依赖上游 HTTP 服务在线。

| 方案 | 评估 |
|---|---|
| A 直接读 `data/store-<center_domain>.json` | 零依赖，但耦合私有文件布局；否决 |
| B `GET /api/store` | 契约稳定，但依赖服务在线、loopback 绑定、超时与鉴权；仅保留为可选在线旁路 |
| **C 带版本快照导出（采用）** | 解耦、可版本化、可审计、可 replay |

## 2. Schema

`contracts/prospect-snapshot.schema.json`（JSON Schema 2020-12）。运行时校验器 `bdl/adapters/snapshot.py` 用纯标准库执行同一套规则：

- `schema_version` 必须是 `1.0.0` 或 `1.1.0`；未知版本拒绝而非忽略。`1.0.0` 文档里出现发现字段同样拒绝：否则版本号就是关于「这份文档能装什么」的谎言。
- 任意层级的未知字段一律拒绝（`additionalProperties: false` 语义）。
- `source.content_hash` 必须形如 `sha256:<64 hex>`。
- `center_domain` 必须与请求的中心域名一致。
- `prospects[].domain` 不得重复。
- 另计算快照文件自身 sha256，以 `file_hash` 写入清单。

不合规快照抛 `SnapshotError`：规范化 JSON 错误结构，不暴露堆栈。

## 3. 字段语义

| 上游字段 | 本包用途 | 禁止 |
|---|---|---|
| `domain` | 主键中的 `target_domain` | |
| `url` | `candidate_url`，站点入口 | **绝不**提升为 `target_url` |
| `type_key` | 映射到 `link-ledger.channel_type` | |
| `priority` | 仅作排期权重 | 不写进台账 |
| `http_status` | 可达性；`null` 记 `gap` | 不猜值 |
| `acquired` | 跳过 | |
| `is_competitor` | 跳过 | |
| `is_gap` | 准入提示 | |
| `note`、`note_en` | 本地运维数据 | 不进外发正文 |

`link-ledger.target_url` 是链接最终出现的第三方页面，由散文证据 `target_url_hint` 推导；未确认前保持 `null` 并写 `gaps`。这是最关键的一条映射：把 `url` 当成落点页，L4 抓取会永远抓错页面。

## 4. type_key → channel_type

| type_key | channel_type |
|---|---|
| `industry_media` | `asset` |
| `guest` | `guest_post` |
| `resource` | `asset` |
| `directory` | `directory` |
| `community` | `community` |
| `social` | `community` |
| `partner` | `other` |
| `authority` | `asset` |
| `digital_pr` | `journalist` |
| `reciprocal` | **排除** |

`reciprocal` 被排除：上游 README 自身警告以互链操纵排名属于 link scheme，R-510 也是白帽硬约束。未知 `type_key` 回退 `other`，不做猜测。

## 5. 导出方职责（上游侧）

本仓库不实现。上游需要：

1. 把 `data/store-<center_domain>.json` 导出为快照形状。
2. 填写 `center_domain`、`schema_version`、`generated_at`、`source.repo`、`source.file` 与 store 文件的 sha256。
3. 输出到本仓库 `data/snapshots/<center_domain>-<timestamp>.json`，或任意由 `--snapshot` 指定的路径。
4. 破坏性字段变更时提升 `schema_version`；消费方拒绝未知版本，而不是静默丢弃字段。

缺失数据在上游报 `unavailable`、在本包报 `gap`；两边都不用 0、false 或估计值顶替。

## 7. 1.1.0 的发现区块

版本 1.1.0 为每个候选增加七个**可选**字段。全部来自 prospector 仓库里 `tools/discover_entry.py` 对站点的只读观测，经 `export-snapshot --enrichment` 传入。都不是必填，所以 1.0.0 的生产端不改也能继续用。

| 字段 | 含义 | 缺失意味着 |
|---|---|---|
| `submission_url` | 承载可用表单的页面绝对 URL | 没人去看过 |
| `form_shape` | `sha256(方法 + 排序后的语义槽位)`——复用键，跑同一插件的站共享 | 没观测到表单 |
| `form_fields` | 线上字段名，含隐藏令牌 | 没观测到表单 |
| `contact_email` | 站点上实际出现的地址 | 没人去看过 |
| `contact_source` | `mailto` / `obfuscated` / `prose` / `form_page` / `manual` | 无地址 |
| `contact_source_url` | 读到该地址的页面 | 无地址 |
| `enriched_at` | 该轮发现的时间 | 从未观测 |

两条规则让这个区块可用：

**缺失不等于空。** `submission_url` 为空表示没去看、或在**页面预算内**没找到，不表示该站没有表单。叠加层里记了页数与 note，读者能区分「没看」与「看了没有」。manifest 里的 `no_channel` 只表示后者。

**发现优先于散文，反过来不成立。** 散文是某个时间点的人工记录，发现是某个时间点对站点的观测。两者并存时以站点为准，因为提交真正打的是站点。但散文仍补空缺：只有散文邮箱、没有发现渠道的域名照样可用。

