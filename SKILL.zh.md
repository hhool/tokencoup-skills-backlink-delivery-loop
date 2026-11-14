---
name: backlink-delivery-loop
description: 用于为某个中心域名准备、执行或复核外链投递闭环：导入 tokencoup-skills-backlink-prospector 的带版本候选快照（store JSON 或 CSV 导出）、把运维散文结构化为联系地址、计算投递节奏与 submitted→live 观察窗、产出仅供审阅的投递清单、记录人工实际执行的投递动作，向发信商只读拉取送达/退信回执，并把事件流投影成每个域名与渠道的当前状态。本 skill 不发邮件、不提交表单。
agent_created: true
---

# 外链投递闭环

## 用途

把某个中心域名的候选外链池，变成**可审阅的投递清单**与完整证据链，再观察外链是否真正上线。本 skill 是**独立分发包**：不会被 WorkBuddy 的 skill 扫描器自动加载，必须显式安装。

**硬边界**：本包从不发送——不提交表单、不发邮件。它的全部网络调用都是只读 GET（L4 页面观测与 L2 发信商回执拉取），`BDL_AUTO_TRANSMIT=true` 会在启动时被拒绝。发送动作保留在人工门之后（R-520；R-530 中 U 类目标的 tier B）。

## 不变量

| 术语 | 定义 |
|---|---|
| `center_domain` | 我们自己的中心域名，例如 `soundbuttons.cc`；由 site slug 解析，不写进封闭的台账行 |
| `target_domain` | 可能承载外链的第三方域名 |
| 主键 | `(center_domain, target_domain, channel)`，`channel` 为 `form` 或 `email`。同一域名可两者兼备，分别计数、分别冷却、分别留证 |
| 投递渠道 | 落点页的属性，不是域名的属性。`form` 需要提交地址，`email` 需要邮箱 |
| 事实源 | `sites/<slug>/07-links/link-ledger.json`，契约 `link-ledger` |
| 终局成功 | `state: live` 且 `verify.outcome: found`（L4）；不存在「投递成功」终态 |

上游快照的 `url` 是**站点入口**，不是 `link-ledger.target_url`。绝不能把它提升为落点页：那会让每次 L4 抓取都抓错页面，产生系统性假阴性。

## 配置

所有行为由环境变量驱动，启动时集中校验一次：

| 变量 | 默认值 | 含义 |
|---|---|---|
| `BDL_HOME` | `.` | 包根目录 |
| `BDL_SNAPSHOT_DIR` | `./data/snapshots` | 导出快照目录 |
| `BDL_PROSE_DIR` | `./assets/prose` | 运维散文目录（抽取联系地址） |
| `BDL_OUT_DIR` | `./data/out` | 清单与回执输出目录 |
| `BDL_HISTORY_DIR` | `./data/history` | 额外的历史文件目录（JSON/JSONL），用于对账 |
| `BDL_TEMPLATES_DIR` | `./data/templates` | 按中心的模板覆盖目录 |
| `BDL_OBSERVATION_DIR` | `./data/observations` | 扫描台账与确认提案输出目录 |
| `BDL_RECORDS_DIR` | `./data/records` | append-only 投递事件日志，每中心一个 `events-<center>.jsonl` |
| `BDL_FORMS_DIR` | `./data/forms` | 表单指纹注册表（`registry.json`） |
| `BDL_CENTER_DOMAIN` | - | 中心域名（多数命令必需） |
| `BDL_OWN_SITE_BASE` | 派生 | 中心站点基址 |
| `BDL_TIER` | `B` | `A` 或 `B`；`A` 需有记录的人类授权人 |
| `BDL_AUTO_TRANSMIT` | `false` | `true` 会被拒绝：发送保留在人工门之后 |
| `BDL_AUTO_CONFIRM` | `false` | `true` 时，命中的 L4 链接在本包台账标 `live` 并进入确认提案队列；共享的 link-ledger 仍不在此写入 |
| `BDL_DEFERRAL_WINDOW_DAYS` | `90` | 按目标域名的重复投递递延窗（R-522 节奏） |
| `BDL_DAILY_CEILING` | `100` | 按目标域名的每日上限（R-530 实例覆盖）。是天花板，不是起步量：发件域要逐周爬坡 |
| `BDL_RECEIPT_WINDOW_DAYS` | `30` | 一次发送或提交最多可以「未回应」多久，超过即结案 |
| `BDL_CONTACT_LOST_DAYS` | `180` | 退信地址的冷却期。挂在地址上，不是挂在域名上 |
| `BDL_PROVIDER` | - | 要拉取回执的发信商：`resend` 或 `postmark`。不设置即人工对账 |
| `BDL_PROVIDER_BASE_URL` | 按发信商 | 覆盖回执 API 基址（本地 mock、区域端点） |
| `BDL_PROVIDER_API_KEY` | - | 回执 API 的只读密钥。不进入配置对象、不打印。缺失即无法拉取 |
| `BDL_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR` |

### 模板（按渠道类型可配置）

`templates.py` 内置了每个 `channel_type`（asset、guest_post、directory、community、journalist、other）的文案，每个都含邮件（`subject` + `body`）与表单（`fields` 字段表）。把 JSON 文件放进 `BDL_TEMPLATES_DIR` 即可覆盖任意渠道：`templates.json` 覆盖所有中心，`<center_domain>.json` 只覆盖该中心。覆盖文件与内置模板做合并——只写想改的部分。渲染会拒绝任何不在上下文中的占位符，以及邮件文案中的任何汉字（R-522）。

## 工作流

1. `doctor` —— 校验配置，确认发送闸门关闭。
2. 产出快照，两种途径二选一：
   - `export-snapshot --store <prospector store-<domain>.json> --out <snapshot.json>` —— 读内部 store。它**没有** `is_gap` 列，因此该字段只能按「非已拥有且非竞品」推导，推导结果会在输出里单独报告。
   - `import-prospects --csv <backlink-prospects-<domain>-<stamp>.csv> --out <snapshot.json>` —— 读 UI 导出，它**有** `is_gap`，且必须直通：这个值编码了本工具无法复现的上游判断（文档页、被标记漏掉的竞品）。重算会把这些行复活成可投目标。`--dry-run` 会报告映射结果：`already_have` → `acquired`、`is_gap` 与 `valid` 取与、以及封闭契约装不下因而被列出的每一列（不静默丢弃）。
3. `validate-snapshot --file <export>` —— 校验 schema 版本、哈希与中心域名。
4. `build-manifest --snapshot <export> --history <file> --history-dir <dir>` —— 读取快照、join 散文联系地址、应用准入与节奏规则、用模板集渲染每行的出站文案，写出 `delivery-manifest.json` 与 append-only 的 `receipts.jsonl`。历史可来自主文件与/或某个目录，全部聚合。
5. `render-template --channel-type <type> --context-json <obj>` —— 预览某行将收到的确切出站文案，不写任何文件。用于在依赖模板前先复核。
6. `lookup --target-domain <domain> [--history ...] [--history-dir ...]` —— 跨所有历史源，对单个目标域名对账已记录的发送/观测。
7. 人工审阅这些行。`ready_for_review` = 有渠道**且**已知落地页；`ready_to_submit` = 有渠道但落地页未知——目录站本来就要等它收录后才产生 listing 页，所以提交与观测是两道闸，不是一道。`no_channel` 表示发现流程已看过该站且既无表单也无邮箱，这行是**结束**，不是待办。任何工具都不得自动执行。
8. `draft --manifest <delivery-manifest.json> --out <dir> [--from <addr>] [--ceiling 0.80] [--domain <d>] [--dry-run]` —— 把 manifest 里的邮件行渲染成可发送的 `.eml` 文件。主题与正文来自渠道模板；来源信息写进 `X-BDL-*` 头与确定性的 `Message-ID`。每个渠道带多份正文，draft 会挑第一份「与**本批次已产出**文案的 5 词 shingle 相似度低于阈值」的；比较前先掩掉域名与 URL——同一模板只换域名，原始相似度约 0.76，会从朴素的 0.80 阈值下溜过去，而对去重器来说就是同一段话。近似重复一律跳过并报出最接近的域名与分数，绝不合并。全程不传输：draft 只是一个文件。
9. `record --target-domain <d> --channel <form|email> --action <...>` —— 记录人工**实际执行**的动作。append-only 且幂等：重复的事件 id 会被拒绝而不是重复写入。`sent` 必须带 `--provider-id`；`submitted` 必须带 `--evidence`；`reply` 必须带 `--reply-class yes|no|pay|reciprocal`。`pay` 与 `reciprocal` 会被记录并标记 `compliance: refused`（R-510）——域名就此关闭，不进入议价。
10. `poll-receipts --provider <resend|postmark> [--auto-confirm] [--dry-run]` —— 向发信商查每个未结案发送的结局。每封信一次 GET，只查「在 `BDL_RECEIPT_WINDOW_DAYS` 内、带 provider id、且尚无定论回执」的发送。`delivered` 与 `bounced` 落成事件；`pending` 与 `unknown` 什么都不写。回执台账写进 `transport-receipts.jsonl`。不加 `--auto-confirm`（且 `BDL_AUTO_CONFIRM=false`）时，事件只排队进 `receipt-proposals.jsonl` 等人工确认。401、404、429、5xx、超时或无法识别的状态词一律记 `unknown`，绝不当退信——被限流的 API 不能把一个活邮箱关 180 天。`--map-status <word>` 只打印某个发信商状态词的映射，不发起调用。
11. `status [--manifest <file>] [--channel <form|email>] [--target-domain <d>]` —— 把事件日志折叠成每个 `(target_domain, channel)` 的当前状态，回答「投放中 / 已投递 / 已上线」。每个状态都带 `next_action`，涉及计时的还带 `window.due_at`。用 manifest 播种时，未被 block 的目标会以 `not_started` 出现，不会有行凭空消失。
12. `forms list|show|register|check|lookup` —— 表单指纹注册表。表单由 `sha256(方法 + action 路径 + 排序后的字段名)` 识别，绝不只看域名：同一站的「联系我们」与「提交资源」字段不同。未见过指纹返回 `needs_human_verify`；`register` 强制要求 `--reviewed-by`，带验证码或需登录的表单存为 `manual_only`。每条记录还带 `slot_map`（线上字段 → 槽位）与 `signature`（把计数器抹掉的字段名），这正是让一份已审阅映射能复用到另一个装同一插件的站点上的关键——见「填充一个提交表单」。
13. `forms prefill --manifest <file> [--snapshot <file>] [--out <dir>]`，随后 `forms adopt --file <receipt> --reviewed-by <who>` —— 为浏览器用户脚本生成取值包，并把一次人工提交产出的映射存进注册表。
14. `plan-window --submitted-at <iso>` —— 计算 T+1/3/7/14/30 天抓取排期。
15. `classify --outcome ... --http-status ...` —— 记录一次观测。403、超时、robots 拒绝记 `unknown`，绝不记 `absent`。
16. `observe --target-url <page> --own-url <our-url>` —— 本地 L4 检测器。仅一次只读 GET：找到指向自身主机的锚点记 `found`；2xx 页面但无该锚点记 `absent`；任何抓取失败（403、超时、robots）记 `unknown`。可用 `--record <file>` 把观测追加为 JSONL。
17. `scan --manifest <delivery-manifest.json> [--auto-confirm] [--out <dir>]` —— 自动扫描。对每个可观测行（含 `target_url` + `own_url`）跑 L4 检测，把结果追加进 `observations.jsonl` 并分类。加 `--auto-confirm`（或 `BDL_AUTO_CONFIRM=true`）时，命中的链接在本地标 `live` 并把确认提案排队进 `confirm-proposals.jsonl`。把共享 link-ledger 标记为 `live` 仍是独立的、需人工确认的 SEXTANT 步骤。
18. `replies --source <.eml|.mbox|目录> [--manifest <file>] [--auto-confirm]` —— 读取导出的回复，逐封匹配到目标域名，并把分类提案排队。见「分类回复」。
19. `serve --manifest <file> [--drafts <dir>] [--packs <dir>] [--prose-dir <dir>]` —— 本地评审界面（仅 127.0.0.1）。只读仪表盘加两个操作者端点：POST `/record`（与 `record` 命令同一套校验，须人工点击表单）和 POST `/import`（上传 prospector CSV，依次执行 `import-prospects` → `build-manifest` → `draft` 并把仪表盘切到新批次）。页面本身绝不发送；`live` 仍只属 L4 判定。

## 投影状态

`status` 每个 `(target_domain, channel)` 输出一个状态：

| 状态 | 含义 | 下一步 |
|---|---|---|
| `not_started` | 已准入，尚未投放 | 补齐渠道 |
| `queued` | 已批准，等待人工发出 | 人工发送 |
| `sent` | 发信商已接收 | 拉回执 |
| `delivered` | 发信商确认送达 | 等回复 |
| `submitted` | 表单已提交且有证据 | 观测落点页 |
| `replied` | 对方回复同意 | 观测后确认 |
| `declined` / `declined_paid` / `declined_reciprocal` | 已关闭，不再触达 | 无 |
| `bounced` | 发信商退信 | 换地址或换渠道 |
| `contact_lost` | 地址在冷却期内 | 等冷却结束 |
| `unanswered` | 窗口结束仍无回复 | 关案或冷却后再试 |
| `unanswered_no_receipt` | 完全没有回执 | 未知，绝不盲目重发 |
| `live` | 已在页面观测到链接（L4） | 写回 link-ledger |

`live` 是唯一的成功状态，且只能来自观测。发送只能证明「发出了」（L1），不能证明链接存在（L4）。窗口内的缺席永远不构成失败（R-512 / R-513）。

## 传输回执（L2）

`sent` 只能证明发信商收下了这封信、并且能指名它。`poll-receipts` 把这个名字换成答案：送达、退信，或仍是未知。

| 发信商状态词 | 台账结果 | 原因 |
|---|---|---|
| `delivered`、`opened`、`clicked` | `delivered` 事件 | 打开不可能先于送达 |
| `bounced`、`complained`、`hardbounce`、`spamcomplaint` | `bounced` 事件 | 地址进入失联冷却 |
| `delivery_delayed`、`queued`、`softbounce` | `pending`，不落事件 | 仍在途中，不是结论 |
| 401 / 403 / 404 / 429 / 5xx / 超时 / 未知状态词 | `unknown`，不落事件 | 读失败不是退信 |

三条约束：

- **有界**。只查 `BDL_RECEIPT_WINDOW_DAYS` 内、带 provider id、且尚无定论回执的发送，因此轮询幂等且不会发散。
- **一事一记**。定论以 `(provider id, action)` 为键，不用发信商并不精确的时间戳；重复轮询不会多写一条。
- **不是提升**。回执属 L2：永不置 `live`，永不写共享 link-ledger，也永不构成对退信地址在冷却期内的重发许可。


## 填充一个提交表单

表单提交无法端到端自动化而仍保持诚实：验证码、条款、致谢页，都需要人来判断。但这道判断**前后**的一切都可以自动化，也确实自动化了：

```
forms prefill  ->  取值包（按槽位给值）  ->  用户脚本填充  ->  人工点击提交
                                                        |
              forms adopt  <-  回执（字段名 + 槽位映射 + 响应页 URL）
                   |
              record --action submitted --evidence <响应页 URL>
```

`forms prefill` 给每条可提交行写一个取值包。取值来自 manifest 行与渠道模板；`message` 与 draft 走同一组组合正文变体，并用同样的 5 词 shingle 阈值对照**本批次已产出**的文案——一个批次里二十个表单，不能带二十段一模一样的话。取值包绝不编造值：缺 `BDL_FROM_EMAIL`、缺落地 URL，或遇到 `phone` 槽位，都停在已记录的 gap 上，而不是一个看似合理的假值。`--snapshot` 提供发现阶段的 `form_shape` 与 `form_fields`；不给时每个包都是 `mapping: none`，由浏览器按 label 猜。

包里的 `mapping` 声明是诚实所在：

| `mapping` | 含义 | 由谁填充 |
|---|---|---|
| `exact` | 同样的方法 + action + 字段集此前已审阅 | 存下的 `slot_map` |
| `shape` | 另一个站点，同样的表单形状**且**同样的字段名签名 | 那个站点的 `slot_map` |
| `none` | 无可复用 | 浏览器启发式，随后人工确认 |

`shape` 复用刻意要求签名与形状同时命中。形状只说「这个表单有 name、email、url、message」，不说哪个线上字段承载哪个槽位。两个 WPForms 站的字段都叫 `wpforms[fields][0..3]`，映射可以迁移；两个槽位相同的手写表单则不行，硬套就会把推广文案写进「订单号」框里。

用户脚本（`assets/userscript/bdl-prefill.user.js`，粘进 Tampermonkey / Violentmonkey）只做填充：给它碰过的每个字段加虚线描边和槽位名提示，列出留给人的部分；绝不点提交、绝不解验证码、绝不填蜜罐。人工提交后，致谢页被捕获为回执：字段名、槽位映射、所填值、响应 URL 与响应文本指纹。

`forms adopt` 拒绝非人工点击（R-520）、无槽位映射、或无响应 URL 的回执——没有致谢页的提交不算证据（L1）。它存下的只是「提交已被站点受理」这一层，绝不等于 `live`：`live` 仍只属于 L4 观测器。随后它打印确切的 `record --action submitted` 命令，事件依旧经由同一道人工审阅的门进入日志。

## 分类回复

整个闭环里最后一步人工操作是读回信。这里自动化的是判断**之前**的部分：把每封信匹配到它的目标域名，并说出正文里指向哪个类的信号。它不做判断。

匹配顺序，证据最强者优先：

1. `In-Reply-To` / `References` 指向我们的某个 `Message-ID`——local part 就是 manifest 的幂等键，恰好解析到一行；
2. `From` 域名是闭环里已出现过的目标域名；
3. 都不匹配：该信进入 `unmatched` 并附带提示，绝不猜测。

分类给出 `reply_class`、**命中的短语**（不是正则）与置信度：

| 类 | 置信度 | 后果 |
|---|---|---|
| `yes` | high 需要两个不同信号 | `--auto-confirm` 可代写事件 |
| `no` | high 需要两个不同信号 | `--auto-confirm` 可代写事件 |
| `pay` / `reciprocal` | 任意 | 永远只是提案：这两类会关闭域名（R-510） |
| 投递状态通知（DSN） | —— | 按第 2 层提议为 `bounced`，附 DSN `Status` 码 |

否定先于匹配处理：「非商业站点不收费用」是 `yes`，不是 `pay`。「很想收录你，但有 50 美元登录费」落在 `pay`——因为邀约与收费是同一句话。

每一条分类都以**提案**形式进入 `reply-proposals.jsonl`，并展示命中信号；事件仍由人工命令写入：

```
record --action reply --reply-class yes --address ... --note "confirmed manually"
```

`--auto-confirm` 只代写高置信度的 `yes`/`no`，绝不代写 `pay`、`reciprocal` 或 `bounced`——这三者都会关闭一些东西，而分类器在「关闭」上犯的错，是把一扇正要打开的门焊死。

## 清单状态

`ready_for_review`（材料齐、待人工门）—— `needs_enrichment`（缺落点页或投递通道，写入 `gaps`）—— `deferred`（90 天重复窗）—— `blocked`（竞品、已拥有、上游 `is_gap=false`、互链、不可达、地址被排除、已有 live 链接）。

`ready_for_review` 与 `needs_enrichment` 是入口状态，不是进度：它们只说明材料是否齐备，不说明是否投放过。进度只看 `status`，永远不看清单。

## 分层

- 入口层 `bdl/cli.py`：只解析参数、格式化 JSON。
- 服务层 `normalize`、`cadence`、`window`、`pipeline`、`templates`、`scan`、`state`、`record`、`receipts`：承载业务规则，不直接碰 HTTP 与文件句柄（`scan`/`observe`/`receipts` 的网络调用被注入在 `Fetcher` / `Reader` 可调用之后）。
- 适配层 `snapshot`、`csv_import`、`prospector_export`、`prose`、`outbox`、`observation`、`events`、`forms`、`provider_events`：承担全部 I/O。
- 横切：集中环境变量校验、类型化错误 + 全局处理、带 `batch_id` 的结构化 JSON 日志、边界输入校验、幂等键、原子写与 append-only 回执与事件。

## References

| 何时加载 | 文件 |
|---|---|
| 字段映射、状态机、L1–L4、观察窗 | `references/state-and-evidence.md` |
| R-520 / R-530 裁决与改规则成本 | `references/rule-boundary.md` |
| 散文抽取、排除、溯源与漂移 | `references/prose-extraction.md` |
| 上游快照契约 C 与类型映射 | `references/upstream-contract.md` |

中文镜像为 `SKILL.zh.md` 与 `references/*.zh.md`。英文为主文档；代码与英文文档不含汉字。
