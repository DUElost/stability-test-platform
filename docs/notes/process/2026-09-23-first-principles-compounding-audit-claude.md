# 第一性原理 × 长期复利全域审计落盘（claude，七域并行）

Status: implemented
Class: process

## Decision

将本轮「第一性原理 + 长期复利」全域审查落盘为
[`PLATFORM_FIRST_PRINCIPLES_COMPOUNDING_2026-09-23_ae232a3_claude.md`](../../reviews/PLATFORM_FIRST_PRINCIPLES_COMPOUNDING_2026-09-23_ae232a3_claude.md)。
基线 `main@ae232a30`，与同日 codex 容量定向稿同基线共存、互不覆写：codex 稿 F01–F08
维持既有立案，本稿只引用编号并标注增量证据，不重复立单。目标规模按
150 host × 25 device = 3750 device 输入；历史文档数字差异按 owner 声明视为待修订事实，
不当缺陷。全程只读：七路子审计 + 主会话对每条 P0 断言独立复核（runners 字典、
retention 状态白名单、读端点写库、准入 FOR UPDATE 形状、merge flock、告警规则集、
Alertmanager receiver、备份行曲线、fix:feat 口径）。

## Alternatives

- 直接以既有 PLATFORM_AUDIT_2026-09-11 / codex 容量稿为终稿不再全域审计：不采纳；
  两稿各覆盖单切面，本次输入（工具生态多语言化 + 150/3750 + 单人维护）要求跨域
  合成判断，尤其「数据复利为零」与「扩展成本双向背离」两条只有并表才可见。
- 把发现直接批量开成 issue：不采纳；本稿是审查不是领单，立单归属与排期由 owner
  裁决（与 codex 稿「复写他人未跟踪审查稿：不采纳」同一纪律）。
- 将结论写进 CLAUDE.md/DOC-MAP 常驻层：不采纳；审查稿属按需层，登记义务只到
  DOC-MAP 收录（未做，留给 owner 决定本稿是否转正）。
- 复用当日 ADR-0033–0051 复审稿覆盖扩展性域：不采纳；该稿问题是 ADR 链自洽性，
  本域问题是边际接入成本实测，正交。

## Verification

- 全部命令为只读：git 统计、grep/sed/wc、`systemctl cat`、`ss -ltnp`、备份 dump 的
  行计数 awk（不读数据内容）、`ai_work.py status`/`declare`、`pytest --collect-only` 未跑
  （由子审计跑的 `check:quick` 24s 全绿与 tests 收集数引用其记录）。
- 未运行：数据库连接、生产端点请求、真实压测、恢复演练——报告中所有速率/规模数字
  标注为模型推导或历史备份实测，无一项宣称容量验收通过。
- 生产 `.env` 实际覆盖、防火墙规则（需 root）、AM 通知真实触达性：未核实，稿内已标。
- **落盘（2026-09-23）**：本稿落入 `docs/reviews/`，落盘时 `main` 已至 `1d1bd33c`；页首追加
  「落盘附注」四则（同批稿均已合入 / P0-7 已由 Phase 1 切根解决 / 缺口登记 #3230 与
  #3231–#3233 / 公开面裁剪），不改写原快照正文。
- **落盘时脱敏（公开仓口径）**：`§2 P1-4` 中三项**可直接利用**的现场指纹——无鉴权告警接收面
  的端口与产品名、开放代理端口与配置项、远程管理与文件共享端口——已改为类别级描述，
  整改判据不变。**原始指纹留档于仓库外** `/home/debian13/private-notes/2026-09-23-u3-redacted-fingerprints.md`
  （不入库、不随 PR 发布，本 Note 亦不复制其内容）。判断依据：上述三项在已合并的同批稿中
  均未出现（比对过 `docs/` 全仓），属新增可利用信息面；类别级表述已足够承载整改方向。

## Revisit

- R0（ADR-0047 裁决、出带告警、submits_dropped 接观测、Phase 1 切根）任一落地后，
  按新基线复审 §2 对应 P0 条目的现势状态。
- 终态持久层（P0-6）若立 ADR，本稿 §6-R2 让位于该 ADR。
- B1/B2 阶梯首跑（合成 150 host / 1×25 真机）产出实测分布后，§1 负载模型应被实测
  替换而非继续引用。
- 本稿已落盘（2026-09-23）；**DOC-MAP 收录未做**——与同波次 U1/U2/U4/U5 同口径（均未登记），
  且本稿 §5 第 5 条主张收敛「登记即完成」的台账义务扩散；是否登记由 owner 另裁。
