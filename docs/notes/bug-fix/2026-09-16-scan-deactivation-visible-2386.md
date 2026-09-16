# scan 的单向反激活必须显式可见，并纳入部署源守卫（#2386）

Status: implemented
Class: bug-fix

## Decision

`POST /scripts/scan` 的输入是 `STP_SCRIPT_ROOT` 指向的那棵树 —— 生产上就是**共享主工作树
的当前检出**（systemd `WorkingDirectory` 即仓库根）；而 scan 对「盘上消失」的已注册版本
做**单向**反激活（目录回来再扫也不复活，需显式重激活）。于是「别的会话把树切到不含某
版本的提交」+「窗口内有人跑 scan」= 主线活跃版本被**静默**吃掉，事后既看不出被吃的是
哪几个，也无法靠再扫恢复。多 Harness 并行时切换是常态：本单开单当天，主工作树在一个
会话内被切过 `docs/r08… → r10… → r11… → r13… → r14…`。

只收口「静默」这一半，不动语义那一半：

1. **明细可见**：`ScanResult.deactivated_versions = [{name, version, nfs_path}]`，
   `deactivated` 计数保留（runbook 的 jq 投影仍可用）。它同时进 **API 响应**与
   `record_audit(details=result.to_dict())` 的 scan 审计 —— 反激活从此可事后归责；
   另有 WARNING 日志（截断到前 20 条，避免极端情况下把日志刷爆）。
2. **scan 前再核一次来源树**：`check-deploy-source.sh` 的适用动作明确加入
   `scripts/scan`，runbook §1.4 在 scan 行**前**再执行一次该脚本 —— 关键点不是
   「§1.1 跑过一次」，而是 **scan 与 restart 之间本身就有窗口**，并发会话随时可能
   把树切走；同时把 jq 投影扩到含 `deactivated_versions`，并写明「列出的若不是
   本次真要退役的，立即回 §1.1，别继续往下走」。
3. 判别依据「执行树 == 目标 revision」原先只存在于执行者的肌肉记忆里（本单开单时
   两次 scan 都是靠人工 `git diff --quiet origin/main -- backend/agent/scripts` 才敢跑），
   现在由第 2 条落到工具上。

## Alternatives

- **改成「不显式确认就不反激活」或「拒绝」**（issue 的修法 2/3）：否决于本单。第 3 条
  会改变既有退役流程，issue 自己就要求先确认 `#735`/ADR-0039 的退役轨道是否依赖当前
  单向语义 —— 那是裁决，不是我能顺手定的默认值翻转。本单因此只交「显式列出」这一支
  （验收判据 1 的括号里明写了 拒绝 / 需确认 / **显式列出** 三种可接受形态之一）。
- **加 `dry_run` 参数**：不解决问题——操作者忘了传就等于没有。
- **只改 runbook 不加明细**：留不住。审计与响应里只有一个 `deactivated: 3`，事后无从
  判断「刚才是不是把主线三个版本吃了」，而这条链的失效恰恰是**只在事后才被发现了**。
- **顺手也防守「退役主机被 scan 影响」等相邻问题**：不同面，不并进来。

## Verification

- `backend/tests/services/test_script_catalog_activation.py` + `backend/tests/api/test_scripts.py`
  合计 **35 passed**，含本单新增/强化的断言：
  - 服务层：盘上缺失 → `deactivated_versions` 逐条点名（name/version，且 `nfs_path`
    要以被判定缺失的那个版本目录结尾）；
  - 服务层反向边界（新增）：没有反激活时明细必须是**空表**，而不是计数 0 + 键缺失，
    `to_dict()` 也要带得上这个键（前端/脚本按存在性取值才不会 undefined）；
  - API 层：`POST /scripts/scan` 的响应里 `deactivated_versions` 逐条可见 —— 钉的是
    **对外契约**，不只是内部 dataclass。
  注：这三条是「强化既有行为的可判别性」，对**旧实现不红**（旧实现确实反激活了，只是
  不点名）。本单不是修一个当前会算错的判据，而是消掉「静默」这个属性，因此红绿自证
  体现在「新键存在且内容正确」上；语义本身（会不会反激活）未动，也就无从红。这一点
  在 PR 里同样明说，不拿「测试全绿」冒充「修好了一个错判」。
- 脚本可运行性：在分支树上执行 `tools/dev/check-deploy-source.sh` → `exit=1` 且报
  「生产工作树在分支 'fix/2386-…'，不在 main」——守卫按预期拒了本 worktree 之外的
  场景，同时证明 scan 前置这行不会把脚本弄挂。
- `python scripts/run_gates.py check:quick`、`ruff`：见 PR。

## Revisit

- **默认值翻转（需裁决）**：把「盘上缺失」从「直接反激活」改为「报告 + 需显式确认」，
  与现有 `force_rebaseline` 逃生阀同风格。前置判断是 `#735`/ADR-0039 的退役轨道是否
  依赖当前单向语义 —— 若依赖，第 2 条（工具守卫）+ 本单的可见性就是终态；若不依赖，
  建议翻默认。**判据落点**：`backend/services/script_catalog.py` 的
  `deactivated_versions` 已是实现该确认所需的全部信息。
- `.claude/skills/control-plane-deploy/SKILL.md` §1.4 也执行同一条 scan，按本单同样
  插一行前置校验才对称；但 Harness 共享规则文件同一时间只由一个 Execution 改
  （AGENTS.md），本单不碰，留待下一次该文件的持有者顺手补。
- 根因面还有 `#1987`（控制面检出双重角色导致发布停摆）：同一棵树既被当部署源又被当
  工作区。终态出口是把 scan 的输入从「工作树」改成「已校验 revision 的只读检出」，
  那需要 ADR 级裁决（本单只把风险显式化，不假装已解决）。
