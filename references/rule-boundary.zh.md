# R-520 裁决口径与改规则成本

> 实现状态：档 1 已确认并在代码中强制（启动拒绝 `BDL_AUTO_TRANSMIT=true`）。英文主文档为 `rule-boundary.md`。

## 1. R-520 原文到底禁止什么

`repos/wb-seo-ops/rules/R-520-link-stage-automation.md`（EN）与 `rules/zh/` 镜像：

- 机器可做：读公开页面判链接存在、算配额、起草文案、把页面开到下一步。
- 机器不可做：注册账号、点确认邮件、提交资料/发帖/投稿、代替人登录。
- **边界条款（关键）**：「本规则只决定谁可以动手：人，或者**有人的授权记录在案的机器**。」
- 判据：「`allowed` 的结论要连同操作、渠道 id、**授权人**一起记录。『脚本自己判断的』不算证据。」
- 另有两条常驻约束：只读公开页面；**一条命令只碰一个渠道**（碰两个即批量，无获批批量）；**拒绝不按计划重试**。

结论：R-520 不是对机器动作的绝对禁令，而是**归属要求**。现有 `AGENT-PLAYBOOK.md` 允许 `autonomy=auto` 的表单提交，正是「人已授权 + 记录授权」这条通道；playbook 同时把邮件轨留在人工、把 `state` 留给人类，与 R-520 不矛盾。

## 2. 已确认裁决（档 1）：不改 R-520 正文

| 档位 | 做法 | 效果 | 成本 |
|---|---|---|---|
| 档 0 | 沿用现有「授权记录 + 一命令一渠道」 | 表单轨自动提交、邮件轨人工点发 | 0 |
| 档 1（已确认） | 在 `console.config.yaml` 声明 R-530 实例级 override，启用 tier B 批量（一次审阅一批，每条一张回执） | 邮件轨由「逐封点」变「按批次点」；R-522 的 1/24h、5/30d 在本实例替换为 100 地址/域/24h，90 天窗保留 | 配置 + 授权记录 |
| 档 2 | tier C 无人值守、定时自动发送 | 完全去掉点击 | **被 R-530 明确禁止** |

R-530 原文：「Tier C on class U is **not enabled** by this rule. `compliance.gate` hard-blocks automated link creation as a non-relaxable mapping of an external platform policy, with an explicit 'the owner may not relax it'. Enabling it needs a separate written ruling that either revises that interpretation or accepts the risk. **An internal override cannot waive an external policy.**」

外链目标站属于 U（uncontrolled，第三方主机）。因此：

- 改 R-520 正文不会带来任何新权限 —— 它本来就允许授权后的机器动作。
- 真正限制无人值守的是 R-530 的 U/tier C 组合与外部平台政策，且内部规则无法豁免外部政策。
- 若要真正无人值守，需要一份**单独的书面裁决**（修订解释或接受风险），并同步 `compliance.gate`；这不是「改一条规则文本」能完成的。

R-530 同时规定 tier C 的归属替代方案：授权人 + rule id + batch id + 时间戳，append-only、可 replay，不接受匿名动作；tier A/B 仍保留 R-520 的具名点击。

## 3. 本包如何强制该裁决

- `bdl/config.py::Config.from_env` 启动即拒绝 `BDL_AUTO_TRANSMIT=true`。
- `bdl/cli.py` 完全没有 send / submit 子命令。
- `bdl/services/pipeline.py` 在每份清单写入 `policy.auto_transmit: false` 与 `human_review_required: true`。
- `outbox.py` 只写本地产物；回执 append-only、可 replay。
- `assert_outbound_copy` 拒绝 CJK 与未替换占位符（R-522）。

## 4. 若确实要改 SEXTANT 规则：执行顺序

来自 `sextant-rule-and-tool`，顺序错误会让 `verify` / `tools` / `tools-doc` 三道 gate 分别变红：

1. 写 EN 规则 + `rules/zh/` 镜像，**frontmatter 必须字节一致**（必需键：`id name name_en when when_en inputs outputs evidence on_missing weight tools`）；EN 正文不得含汉字。
2. 规则若由脚本执行，先把脚本注册进 `repos/wb-seo-hub/tools/registry.json`（`status: in_use` 要求 `used_by` 路径真实存在 + ISO `verified_at`），再把工具 id 写进两份 frontmatter 的 `tools:`；不可留 `tools: []`。
3. bump `repos/<repo>/VERSION` **和**两份 `SKILL.md` / `SKILL.zh.md` 的 `version:`。
4. 双语 `CHANGELOG.md` / `CHANGELOG.zh.md` 各加一条。
5. 重渲染工具总账：`dump_tools.py --doc docs/10-... --lang en|zh`，手改两份 docs/10 头部工具计数，并 grep 旧数字（docs/09、11、12、README.zh.md 也有）。
6. 三道 gate 依次跑：`./bin/sextant tools` → `tools-doc` → `verify`；`verify` 需后台运行（约 50 s），`boss_check` 约 30 分钟，勿前台跑。
7. 包树改完后 `./install.sh ~/.workbuddy/skills all` 同步安装副本（镜像覆盖，约 11 分钟，后台跑）；否则 gate 读包树、控制台读旧副本，出现「代码正确但全红」。

新增契约或给 `state` 加枚举属于 contracts minor bump：还需 `VERSION-MATRIX.yaml`（含 `repo_versions` 行）、12 个 `contracts.lock`、各 SKILL frontmatter 的 `contracts.hub`、`tests/test_contracts.py::SchemaSetTests.PUBLISHED`、包级 `__version__` / `pyproject.toml` / `CODEX.yaml` / README / docs/00 / docs/12（含 schema 计数与 D 编号双语）。**未定型前不要冻结 schema**（见 `state-and-evidence.md` 方案 A）。

## 5. 其他常驻约束

- R-510：白帽、零付费、禁止批量模板/伪造身份/买链/PBN、禁止自动注册。
- R-522：按 pitch 计数而非行数；`directory/listing/profile/registry` 类行不占额度；CJK 禁止；正文必须指名对方页面。
- R-530：地址分级 L1 语法 / L2 域名 MX / L3 `provider_id` / L4 `last_event`；未检出的地址是 `gap`，不是 0、不是 failure；相同正文跨地址仍禁止。
- R-414：回执必须真实，不得伪造。
- 禁改期（至 2026-11-22）冻结的是站点结构性改动（head/title/模板/内链/变现位）；本包不改站点 head，但仍需在入口检查 `window_state`。
