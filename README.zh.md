# 外链投递闭环

为某个中心域名准备并记录外链投递，再观察外链是否真正上线。仅依赖 Python 标准库；不提交表单、不发送邮件——所有网络调用都是只读 GET（页面观测、发信商回执）。

英文为主文档；`README.zh.md` 与所有 `*.zh.md` 是中文镜像。代码、英文文档与英文注释不含汉字。

- 本地仓库名：`backlink-delivery-loop`（短名，与 `mqtt-coop` 一致）
- 远端仓库名：`tokencoup-skills-backlink-delivery-loop`
- 上游：`tokencoup-skills-backlink-prospector`
- 链接状态事实源：SEXTANT `07-links/link-ledger.json`

---

## 1. 本包做什么

| 做 | 不做 |
|---|---|
| 消费带版本的候选快照（契约 C） | 直接读上游 store 文件 |
| 从运维散文结构化联系地址并保留溯源 | 未经人工确认就采信地址 |
| 应用准入、节奏与重复窗规则 | 注册账号或代登录 |
| 写仅供审阅的 `delivery-manifest.json` | 发信或提交表单 |
| 追加可审计的 JSONL 回执 | 写 `link-ledger.state` |
| 计算 submitted→live 观察窗排期 | 发送、重发或自动重试任何东西 |
| 观测目标页是否有 live 链接（L4，只读） | 用短窗口判定失败 |
| 拉取发信商的送达/退信回执（L2，只读） | 把回执当成 `live` 结论 |
| 构建独立分发 zip | 自动安装进 `.workbuddy/skills` |

`BDL_AUTO_TRANSMIT=true` 在启动时即被拒绝。这落实了已确认的 R-520 裁决：不改规则正文，对 U 类目标采用 R-530 实例级 override + tier B（每批次审阅一次）。

## 2. 已确认的设计决策

1. 本地目录用短名；远端保留 `tokencoup-skills-` 前缀。
2. 独立分发包：无符号链接、不自动加载。
3. 对接契约 C：带版本快照导出，只读消费。
4. 本次交付包含本地首次 git 提交。
5. 双语文档、schema、许可证、忽略规则与上游契约一并补齐。
6. 确认 R-520 档 1（R-530 实例级 override + tier B）；不修改 R-520。

## 3. 环境要求

- Python 3.10 及以上
- 无第三方依赖，不需要 `pip install`
- 不提交 `.env`：从 `.env.example` 复制后仅保存在本机

## 4. 快速开始

```bash
cd backlink-delivery-loop

export BDL_HOME=$PWD
export BDL_CENTER_DOMAIN=soundbuttons.cc
export BDL_OWN_SITE_BASE=https://soundbuttons.cc
export BDL_PROSE_DIR=$PWD/assets/prose
export BDL_OUT_DIR=$PWD/data/out
export BDL_AUTO_TRANSMIT=false
export BDL_TIER=B

python3 bin/delivery doctor
python3 bin/delivery validate-snapshot --file assets/prospect-snapshot.example.json
python3 bin/delivery build-manifest \
  --snapshot assets/prospect-snapshot.example.json \
  --history assets/send-history.example.json

python3 bin/delivery plan-window --submitted-at 2026-11-14T12:00:00Z
python3 bin/delivery classify --outcome absent --http-status 403
python3 bin/delivery package --out dist
```

示例数据产出 9 行：3 条 `ready_for_review`、1 条 `needs_enrichment`、1 条 `deferred`、4 条 `blocked`。全程无投递动作。

## 5. 命令

| 命令 | 用途 |
|---|---|
| `doctor` | 校验配置，打印路径与策略状态 |
| `export-snapshot --store ... --out ...` | 契约 C 生产端：读 prospector store，产出带版本快照 |
| `import-prospects --csv ... [--out ...] [--dry-run]` | 读 prospector 的 CSV 导出（信息更全，含 `is_gap`），报告映射并产出快照 |
| `validate-snapshot --file ...` | 执行快照契约校验 |
| `build-manifest --snapshot ... [--history ...] [--history-dir ...]` | 产出审阅清单 + 回执；每行内嵌渲染后的文案 |
| `render-template --channel-type ... --context-json ...` | 预览某行将收到的出站文案 |
| `lookup --target-domain ... [--history ...] [--history-dir ...]` | 对单个目标域名对账已记录的发送 |
| `record --target-domain ... --channel form\|email --action ...` | 记录一次投递动作；append-only 且幂等 |
| `poll-receipts --provider resend\|postmark [--auto-confirm] [--dry-run]` | 拉取未结案发送的回执（L2，每封一次 GET） |
| `status [--manifest ...] [--channel ...] [--target-domain ...]` | 把事件日志折叠为每个域名、每个渠道的当前状态 |
| `forms list\|show\|register\|check` | 表单指纹注册表；首次遇到必须具名人工复核 |
| `plan-window --submitted-at ...` | T+1/3/7/14/30 天排期 |
| `classify --outcome ... --http-status ...` | 把一次观测判为 found/absent/unknown |
| `observe --target-url ... --own-url ... [--record ...]` | 本地 L4 检测器（只读 GET） |
| `scan --manifest ... [--auto-confirm] [--out ...]` | 自动扫描目标页、记录观测、可选排队确认提案 |
| `package --out ...` | 生成 `dist/backlink-delivery-loop.zip` |

所有命令向 stdout 输出结构化 JSON；日志是 stderr 上带 `batch_id` 的结构化 JSON；错误是规范化 JSON（含 `code`），不暴露堆栈。

### 配置（环境变量）

`BDL_HOME`、`BDL_SNAPSHOT_DIR`、`BDL_PROSE_DIR`、`BDL_OUT_DIR`、`BDL_HISTORY_DIR`、
`BDL_TEMPLATES_DIR`、`BDL_OBSERVATION_DIR`、`BDL_RECORDS_DIR`、`BDL_FORMS_DIR`、
`BDL_CENTER_DOMAIN`、`BDL_OWN_SITE_BASE`、`BDL_TIER`（`A`/`B`）、
`BDL_AUTO_TRANSMIT`（必须为 `false`）、`BDL_AUTO_CONFIRM`（默认 `false`）、
`BDL_DEFERRAL_WINDOW_DAYS`（默认 `90`）、`BDL_DAILY_CEILING`（默认 `100`）、
`BDL_RECEIPT_WINDOW_DAYS`（默认 `30`）、`BDL_CONTACT_LOST_DAYS`（默认 `180`）、
`BDL_PROVIDER`、`BDL_PROVIDER_BASE_URL`、`BDL_PROVIDER_API_KEY`、`BDL_LOG_LEVEL`。
完整列表见 `.env.example`。

### 投递渠道 ≠ 渠道类型

`channel_type`（asset、guest_post、directory、community、journalist、other）决定文案；
`channel`（`form`、`email`）决定怎么投，且是落点页的属性。同一域名可两者兼备，
此时清单行 `channels: ["form", "email"]`，`status` 分两条独立跟踪。

### 状态投影

清单是照片，`status` 是胶片。`record` 为每个实际执行的动作追加一条事件，
`status` 把这些事件折叠成每个 `(target_domain, channel)` 的当前状态：

```
record --target-domain mixkit.co --channel email --action sent \
       --address hello@mixkit.co --provider-id msg_123
record --target-domain mixkit.co --channel email --action bounced --address hello@mixkit.co
status --manifest data/out/delivery-manifest.json
```

`sent` 必须带 provider id；`submitted` 必须带证据；`reply` 必须带分类。
`pay` 与 `reciprocal` 会以 `compliance: refused`（R-510）入库，域名就此关闭而非议价。
退信把**地址**放进 180 天冷却，但不关闭该域名的另一条渠道。30 天内没有回执记
`unanswered_no_receipt`——是未知，不是失败，也不是重发的理由。

### 传输回执（L2）

`sent` 只能证明发信商收下了信、并且能指名它。`poll-receipts` 把这个名字换成答案。

```bash
export BDL_PROVIDER=resend
export BDL_PROVIDER_API_KEY=re_readonly_key

python3 bin/delivery poll-receipts --records-dir data/records            # 只排提案
python3 bin/delivery poll-receipts --records-dir data/records --auto-confirm
python3 bin/delivery poll-receipts --provider resend --map-status complained
```

| 发信商状态词 | 结果 | 原因 |
|---|---|---|
| `delivered`、`opened`、`clicked` | `delivered` 事件 | 打开不可能先于送达 |
| `bounced`、`complained` | `bounced` 事件 | 地址进入失联冷却 |
| `delivery_delayed`、`queued` | `pending`，不落事件 | 仍在途中，不是结论 |
| 401 / 404 / 429 / 5xx / 超时 / 未知词 | `unknown`，不落事件 | 读失败不是退信 |

只查 `BDL_RECEIPT_WINDOW_DAYS` 内、带 provider id、且尚无定论回执的发送，因此轮询有界且幂等：
定论以 `(provider id, action)` 为键，不用发信商并不精确的时间戳。全程只读：每封信一次带鉴权的
GET，绝不重发。回执永不置 `live`。

`--fixture <file>` 用录好的响应集替代真实调用，是在离线环境排练一次轮询的方式
（见 `assets/provider-events.example.jsonl`）。

### 表单指纹注册表

表单由 `sha256(方法 + action 路径 + 排序后的字段名)` 识别，不看域名：同一站的
「联系我们」与「提交资源」字段不同。未见过的指纹返回 `needs_human_verify`；
`forms register` 强制 `--reviewed-by`，并保存字段映射、成功判据与验证码/登录门
（存为 `manual_only`）。没有任何路径会自动注册表单，提交也仍需人工点。

### 模板

内置了每个 `channel_type` 的文案。通过 `BDL_TEMPLATES_DIR` 覆盖任意渠道：
`templates.json` 覆盖所有中心，`<center_domain>.json` 只覆盖一个。见
`assets/templates.example.json`。渲染拒绝未定义的占位符与邮件文案中的汉字（R-522）。

### 自动确认

`scan --auto-confirm`（或 `BDL_AUTO_CONFIRM=true`）把命中的链接在本地台账标 `live`，
并排队确认提案。它不写共享的 link-ledger——把共享状态标 `live` 仍是独立的、需人工确认的 SEXTANT 步骤。

## 6. 产物

`data/out/delivery-manifest.json`：

```json
{
  "center_domain": "soundbuttons.cc",
  "batch_id": "...",
  "policy": {"tier": "B", "auto_transmit": false, "human_review_required": true},
  "summary": {"total": 9, "by_status": {"...": 0}},
  "rows": [{"target_domain": "beebom.com", "status": "ready_for_review", "gaps": []}],
  "gaps": []
}
```

`data/out/receipts.jsonl` 为 append-only，`data/observations/transport-receipts.jsonl` 亦然
（每封被查询的信一行，包括什么都没查到的那些）。前者记录「某次清单被生成」，后者记录
「发信商说了什么」。两者都是派生产物，不是台账；`data/` 已加入 `.gitignore`。

## 7. 架构

```
bin/delivery                 入口（薄封装）
bdl/cli.py                   控制器：解析参数、格式化 JSON、全局错误处理
bdl/services/                业务规则，不依赖 HTTP 类型与文件句柄
  normalize.py               准入、类型映射、幂等键、CJK 闸门
  cadence.py                 90 天窗、按域 24h 上限
  window.py                  观察窗排期、unknown != absent
  pipeline.py                编排
  state.py                   事件流 -> 每 (域名, 渠道) 一个状态 + 计时器
  record.py                  一条已校验、幂等的事件
  observe.py / scan.py       L4 检测器与自动扫描
  receipts.py                L2 回执的排期、拉取与落库
bdl/adapters/                全部 I/O
  snapshot.py                契约 C 校验器
  prose.py                   带溯源的散文抽取
  outbox.py                  原子写、append-only 回执、历史读取
  events.py                  append-only 投递事件日志
  observation.py             观测台账与提案队列
  forms.py                   表单指纹注册表
  provider_events.py         发信商回执 API 读取器（可注入）
bdl/config.py                集中环境变量校验、快速失败
bdl/errors.py                类型化错误
bdl/log.py                   结构化 JSON 日志 + 脱敏
bdl/models.py                纯 dataclass
bdl/package.py               分发 zip
```

横切能力：边界输入校验、类型化错误 + 全局处理、原子写、幂等键、结构化日志、YAML/JSON 中不放密钥。

## 8. 契约与规则

- `contracts/prospect-snapshot.schema.json` —— 上游快照，版本 `1.0.0`
- `references/upstream-contract.md` —— 字段映射，以及 `url` 为何不是 `target_url`
- `references/state-and-evidence.md` —— L1–L4、台账字段、观察窗
- `references/rule-boundary.md` —— R-520 / R-530 裁决与改规则成本
- `references/prose-extraction.md` —— 抽取、排除、漂移

相关规则：R-510、R-512、R-513、R-520、R-522、R-530、R-414。

## 9. 测试

```bash
PYTHONPATH=$PWD python3 -m unittest discover -s tests -t . -v
python3 -m compileall -q bdl bin tests
```

共 172 项离线单测与 CLI 测试，覆盖 schema 拒绝、CSV 导入映射、散文溯源、字段映射、节奏、观察窗、模板、历史对账、扫描/自动确认台账、事件幂等、状态投影与两个计时器、表单指纹、发信商状态映射、回执轮询及其幂等、发送闸门、打包与 CLI 输出。测试不联网，也不 mock 服务层。

## 10. 分发

`bin/delivery package` 生成 `dist/backlink-delivery-loop.zip`，排除 `.git`、`data/`、`out/`、`dist/`、`__pycache__`、`.env`、密钥与临时文件。分发包需显式安装，不自动加载。

## 11. 刻意未实现

- 表单提交与邮件发送（人工门，R-520 / R-530 tier B）。渲染出的文案只为审阅而生成，从不发送。
- 写 `link-ledger.state`（单一人工写入器，R-512）。`scan --auto-confirm` 只在本地观察台账标 `live` 并排队提案。
- 契约 C 的上游导出端（即 `backlink-prospector` 内部产出本包所消费快照的生产者）。
- MX 解析与 SMTP 级地址校验（L3）。`poll-receipts` 只读发信商已知的东西（L2），不探测邮箱。
- 表单预填与自动提交。`forms register` 只存已复核的字段映射；填写与点提交仍是人工。
- 回信解析。`record --action reply` 接收人工给出的分类；没有任何代码读邮箱。

## 12. 许可

MIT，Copyright (c) 2026 hhool。
