# 控制面部署 SOP 校准（2026-09-22 四段实跑）+ 新版本「下发」步骤入契约

Status: implemented
Class: process

## Decision

2026-09-22 按 `control-plane-deploy` skill 端到端跑了一遍生产部署（后端 `pull` + 迁移核对 +
`restart` → 前端干净 worktree 换包 → `scripts/scan` → 48 台 fleet 热更新，收口 `45c159cf`）。
skill 是 v0 骨架、自带「下一次真实部署时以当天实况校准」的约定，本次按实跑逐条校准，并把
实跑暴露出的流程缺口补进权威契约：

**A. `control-plane-deploy` skill**

1. 头部状态由「v0 骨架，全部待校对」改为「§0/§1/§1.5/§2/§3 已真机校准，残余 ⚠️ 只标在
   未实跑条目上」，并要求后续改动在同一 PR 追加 §7 记录行（校准来自实跑，不来自推演）。
2. §0 **推翻** 09-15 行记的「`/hosts/{id}/hot-update` 是 `{data: …}`」：该路由没有
   `response_model`，实测回**顶层裸对象** `{"ok":true,…}`；按旧描述写 `jq '.data.ok'`
   恒为 null。（`/api/v1/hosts` 不分页时是裸数组，`/scripts/scan`、`/script-presence/*`
   才是 `{data: …}`。）
3. §3 重写判据与耗时：
   - **判据是 digest 不是 revision**（ADR-0040 v1.1）：`agent_code_sync_status` 只由 code
     artifact digest 决定；`agent_code_revision`/`expected_code_revision` 是溯源文本
     （`expected` 取仓库 HEAD，任何不动 `backend/agent/**` 的提交都会让它前进）。
   - **desired digest = 现算当前工作树（含未跟踪文件）**：并发会话在 `backend/agent/` 下
     留一个临时文件即可让全 fleet 变 drift（实测 `sha256:020a7f12…` → `sha256:96ba11d1…`，
     删掉即复原）⇒ 见整片 drift 先查工作树。
   - **自愈**：收敛后远端 `write-digest` 写的是控制面 desired digest（`host_updater.py:380-384`），
     所以一次成功推送必然置 `matched`。
   - **~3s/台**（canary `duration_ms=3068`；48 台约 4 分钟，`SUMMARY ok=48 converged=1
     fail=0 skipped=0`）——旧稿「约 20s/台」过时。
   - 补 code 载荷口径（`tests/`、`test_*.py`、`resources/**` 等排除）与
     「`scripts/` 的新版本靠这一步才落到主机」。
4. §2 补「scan 只写控制面注册表，主机生效必须再跑 §3」；§1 守卫描述补载荷根未跟踪文件。
5. §6 **推翻** 08-31 的「热更新清带外资源」：`stp_agent_priv.PROTECT_ONLY_PATHS =
   ["resources/***"]`（#1950/#2019，契约测试锁定）已把整棵 `resources/` 设为 protect-only
   （只防删、不做 exclude，必须写 `***`）；`resources/` 之外的带外文件仍会被 `--delete`
   抹掉。同表新增「载荷根未跟踪文件」行（#3112 硬失败）。
6. 踩坑守卫区补两条负向约束：载荷根未跟踪文件即停；**DB `active` / `matched` 都不等于
   「主机上有这个脚本文件」**。

**B. 新版本「下发」步骤进权威契约与 SOP**

`script-version-lifecycle` skill 的 §A 到 scan 注册为止，全文没有「下发/生效/热更新」字样；
而脚本是**从主机本地树执行**的（`Script.nfs_path` = `/opt/stability-test-agent/agent/scripts/…`，
`backend/agent/pipeline_engine.py` 直接用该路径起进程）⇒「DB 里 active」≠「主机能跑」。
本次 #3085 的 `fill_storage` v1.1.1 正是如此：已 active，47 台主机仍停在一天前的载荷。

- `docs/development/script-versioning.md`（权威契约）§「新版本上线收尾」：控制面侧由 3 步
  扩为 4 步，插入「4. 下发到主机」（canary → `batch_hot_update --direct` → 逐台
  `agent_code_sync_status=matched` 收口），并写明判到位**不要**看 `script-presence` 的
  `missing=0`（无 Plan 引用的新版本不在账本全集内，#3111）；收尾结论补「未下发前主机侧
  也没有文件」。
- `script-version-lifecycle` skill：§A 新增第 5 步（下发）、description 补触发时机
  「新版本合并后要把文件下发到主机」、后置验证新增「到位」条、踩坑守卫新增
  「DB `active` ≠ 主机可用」。
- `docs/production-minimum-deployment-checklist.md` 与
  `docs/operations/2026-08-29-post-review-deploy-runbook.md` 里守卫判据的描述同步为
  「含载荷根未跟踪文件」。

## Alternatives

- **只改 skill、不动 `script-versioning.md`**：否决。skill 自己写着「权威契约：
  `docs/development/script-versioning.md`」——下发是版本生效链的一部分，只写在 skill 里
  会让权威契约继续漏一步，下次仍会有人照契约做完 3 步就以为收尾。
- **把校准记录写成一份新文档而不是改 skill**：否决。校准记录表（§7）是 skill 自带机制，
  拆成外部文档会让「下一次部署前读哪份」又多一个分叉。
- **顺手把 08-31 那条坑行删掉**：否决。历史校准行是记录（当时确实成立），按本表既有惯例
  （09-15 行反过来修正 08-30 行）保留原文并标注「该形态已被修」，可追溯。
- **把「未覆盖版本判 missing」也写进 SOP 作为判据**：否决。见
  `docs/notes/bug-fix/2026-09-22-script-delivery-truth-3111-3112.md` 的 Alternatives——
  那会造成成片假缺口，SOP 里只给「不要用 missing=0 判到位」这条读法。

## Verification

- 本 Note 的每条改动都对应本次实跑的观测，不是推演：`45c159cf` 全链路（后端三个
  `ExecStartPre` 全 `SUCCESS`、`/health` healthy、`alembic_version == head == 9f8e7d6c5b4a`、
  前端 nginx 服务的 index 与磁盘一致且新特征串 `总容量 (/data)` 命中服务的 chunk、
  scan `created=0 skipped=206 conflicts=[] deactivated=0`、48 台 `SUMMARY ok=48 converged=1
  fail=0 skipped=0` 且逐台 `matched`、设备 862/611 ONLINE 未掉）。
- 行号类事实（`host_updater.py:380-384`、`pipeline_engine.py` 用 `nfs_path` 起进程、
  `PROTECT_ONLY_PATHS`、`resolve_agent_code_sync_status`）逐条在本仓当前 HEAD 上核对过。
- 门禁：`python scripts/run_gates.py check:quick`（见 PR 说明）。
- 未验证项如实标注：§6 的 SP Flash Tool 依赖行仍是 `⚠️待校对`；§3 带外文件那条标了
  `⚠️待校对（未逐台核对带外目录内容）`——本轮只确认了 `resources/***` 的保护来自代码与
  契约测试，没有逐台盘点 48 台主机的 `resources/` 实际内容。

## Revisit

- **下一次真实部署**：按新头部约定在本表追加记录行；若 §3 的 ~3s/台 随 host 数或网络变化
  明显偏移，更新该数字而不是删掉。
- **`⚠️待校对` 的两条**：§6 SP Flash Tool 依赖需一台缺库主机实跑；带外资源保护需要一次
  带外 APK 在场时执行的热更新来实证（本轮没做，故保留标注）。
- **判据唯一性依赖 ADR-0040 v1.1**：若之后有人把 revision 重新拉回判等（历史上 #2057 修过
  一次），§3 的这段说明与 skill 里「别拿它判等」必须同批改。
- 前端部署段（§1.5）本轮走的是「worktree 构建 + 同盘双 rename」，与
  `production-minimum-deployment-checklist.md` §3.4 提到的 `npm run build:prod` 并存；
  两条路径若长期共存，需要一次收敛裁决（本轮未动，属既有双轨）。
