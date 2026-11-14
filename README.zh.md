# 外链投递闭环

为某个中心域名准备并记录外链投递，再观察外链是否真正上线。仅依赖 Python 标准库；不联网、不提交表单、不发送邮件。

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
| 计算 submitted→live 观察窗排期 | 抓取目标页（留给规则承载的后台 worker） |
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
| `validate-snapshot --file ...` | 执行快照契约校验 |
| `build-manifest --snapshot ... [--history ...] [--history-dir ...]` | 产出审阅清单 + 回执；每行内嵌渲染后的文案 |
| `render-template --channel-type ... --context-json ...` | 预览某行将收到的出站文案 |
| `lookup --target-domain ... [--history ...] [--history-dir ...]` | 对单个目标域名对账已记录的发送 |
| `plan-window --submitted-at ...` | T+1/3/7/14/30 天排期 |
| `classify --outcome ... --http-status ...` | 把一次观测判为 found/absent/unknown |
| `observe --target-url ... --own-url ... [--record ...]` | 本地 L4 检测器（只读 GET） |
| `scan --manifest ... [--auto-confirm] [--out ...]` | 自动扫描目标页、记录观测、可选排队确认提案 |
| `package --out ...` | 生成 `dist/backlink-delivery-loop.zip` |

所有命令向 stdout 输出结构化 JSON；日志是 stderr 上带 `batch_id` 的结构化 JSON；错误是规范化 JSON（含 `code`），不暴露堆栈。

### 配置（环境变量）

`BDL_HOME`、`BDL_SNAPSHOT_DIR`、`BDL_PROSE_DIR`、`BDL_OUT_DIR`、`BDL_HISTORY_DIR`、
`BDL_TEMPLATES_DIR`、`BDL_OBSERVATION_DIR`、`BDL_CENTER_DOMAIN`、`BDL_OWN_SITE_BASE`、
`BDL_TIER`（`A`/`B`）、`BDL_AUTO_TRANSMIT`（必须为 `false`）、`BDL_AUTO_CONFIRM`
（默认 `false`）、`BDL_DEFERRAL_WINDOW_DAYS`（默认 `90`）、`BDL_DAILY_CEILING`
（默认 `100`）、`BDL_LOG_LEVEL`。完整列表见 `.env.example`。

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

`data/out/receipts.jsonl` 为 append-only。两者都是派生产物，不是台账；`data/` 已加入 `.gitignore`。

## 7. 架构

```
bin/delivery                 入口（薄封装）
bdl/cli.py                   控制器：解析参数、格式化 JSON、全局错误处理
bdl/services/                业务规则，不依赖 HTTP 类型与文件句柄
  normalize.py               准入、类型映射、幂等键、CJK 闸门
  cadence.py                 90 天窗、按域 24h 上限
  window.py                  观察窗排期、unknown != absent
  pipeline.py                编排
bdl/adapters/                全部 I/O
  snapshot.py                契约 C 校验器
  prose.py                   带溯源的散文抽取
  outbox.py                  原子写、append-only 回执、历史读取
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

共 77 项离线单测与 CLI 测试，覆盖 schema 拒绝、散文溯源、字段映射、节奏、观察窗、模板、历史对账、扫描/自动确认台账、发送闸门、打包与 CLI 输出。测试不联网，也不 mock 服务层。

## 10. 分发

`bin/delivery package` 生成 `dist/backlink-delivery-loop.zip`，排除 `.git`、`data/`、`out/`、`dist/`、`__pycache__`、`.env`、密钥与临时文件。分发包需显式安装，不自动加载。

## 11. 刻意未实现

- 表单提交与邮件发送（人工门，R-520 / R-530 tier B）。渲染出的文案只为审阅而生成，从不发送。
- 写 `link-ledger.state`（单一人工写入器，R-512）。`scan --auto-confirm` 只在本地观察台账标 `live` 并排队提案。
- 契约 C 的上游导出端（即 `backlink-prospector` 内部产出本包所消费快照的生产者）。
- MX 解析与 provider 事件轮询（L2–L4 地址级别）。
- 契约 C 的上游导出侧
- MX 解析与 provider 事件轮询（地址 L2–L4 分级）

## 12. 许可

MIT，Copyright (c) 2026 hhool。
