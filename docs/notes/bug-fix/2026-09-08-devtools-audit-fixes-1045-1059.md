# dev 工具链审计修复批：#1045-#1047 / #1056-#1059

Status: implemented
Class: bug-fix

## Decision

一个 PR 内按单修七处（六文件），全部为 dev 工具链审计（2026-09-08）确认的缺陷，逐单对应：

- **#1045** `check_destructive_git.py` 判定从「段首 git + 整段文本正则」改为 token 级 argv 解析：跳过 env 前缀与 git 全局选项（`-C/-c/--git-dir/--work-tree/--namespace/--super-prefix` 连值跳过，其余 `-` 开头单跳）后取子命令 token。`reset --hard` 判定=子命令 reset 且其余 token 含精确 `--hard`；stash 放行判定=stash 后第一个非选项 token ∈ {list,show,pop,apply,branch}，裸 stash / 未知识子命令 fail-closed。
- **#1046** `run_gates.py` `FULL_EXCLUDE` 增 `harness-ingest`（维持该 gate 注释「不进 quick/pr/full」语义，采纳提单二选一中的推荐项）；`harness_probe.py` `{prompt}` 填充改经 `shlex.quote`（`build_command` 纯函数），消除含空格 prompt 被 shell 分词、CLI 升级即静默 UNGRADED 的面。
- **#1047** `check_invariant_diff.py` 新增行匹配跳过行首 `#`/`//`/`--` 注释行；docstring 不做逐行状态机（diff 行缺文件上下文），边界在 docstring 注明、误报走 `--advisory`。
- **#1056** `ai_work.py cmd_resume` 放行判定前先 `derive_integration` 向 GitHub 刷新（`pr_number` 在场时）；刷新不可达即 REFUSED 提示先 update，不基于已知可能陈旧的缓存放行；registry 不回写（保持 resume 唯一写=生命周期回退，收敛留给 update）。
- **#1057** `check_pr_migrate.py` 端口改 `docker run -P` 随机映射 + `docker port` 回读（`host_port` 纯函数解析）；容器启动失败从 `[SKIP]`（exit 0 假绿）改 FAIL——随机端口消除 pid%1000 碰撞面后，run 失败只剩异常态，不配豁免。
- **#1058** `check_governance_surface.py` S12 三项：record_tip 续行只认行首版本 token（`**vX.Y` 形态兼容，行尾引用 token 不再当末项；标题行保持全行末项以兼容 ADR-0024 式单行罗列）；README 状态 cell 锚定链接 cell 之后；DOC-MAP 行在场但零版本 token 从静默跳过改 BLOCK。
- **#1059** `ai_work.py` codec：`normalize_requirement_id` 拒换行（与 `:` 同款理由）；`_quote`/`_unquote` 受限转义集加 `\n`/`\r`（对称），值内换行落盘转义、读回还原，消除「写入成功读回锁死」。

## Alternatives

- #1045 曾考虑保留正则、仅加 git 选项的前缀豁免正则——否：正则叠补丁无法同时消误伤与绕过，token 级是唯一同时满足两向的粒度。
- #1046 备选「改注释接受夜间成本」——否：harness-ingest 每形态一次真实 LLM 会话，夜间全量引入外部依赖波动与成本，与该 gate 自述的注意力预算纪律矛盾。
- #1056 备选「resume 后由 update 收敛不前置刷新」——否：复活已出窗 execution 期间在窗查重会错误阻塞他人领同 issue，守卫必须读刷新值。备选「不可达时放行缓存值」——否：放行的前提值已知可能陈旧。
- #1057 备选「保留猜端口 + 端口冲突重试」——否：`-P`+回读从结构上消除碰撞面且代码更短；重试仍把「起不来」留在 SKIP 语义里。
- #1058 备选「record_tip 全改行首锚定」——否：会误红 ADR-0024 式单行罗列（真 tip=行内末项）；混合口径（标题行末项 + 续行行首）对存量零行为变化。

## Verification

- 各脚本 `--self-test` 全过（check_destructive_git 新增 #1045 红绿 11 例；invariant-diff 新增注释豁免 4 例；harness_probe 新增 build_command argv 还原 8 例；governance_surface 新增 S12 辅助函数 10 例——此前零覆盖；ai_work 新增 #1059 换行拒绝/往返 3 断言；check_pr_migrate 新增 host_port 解析 4 例）。
- #1045 端到端：真实 hook JSON 逐场景复测——三个绕过形态（`git -C/--no-pager/-c`）现 exit 2，误伤形态（commit 参数含禁令词、`stash --quiet list`）exit 0，原红绿不回退。
- #1056 端到端（临时假仓库 + 真实 MERGED PR #1032，不触真实 registry）：修复前 `resume` 放行（问题复现）；修复后 `[REFUSED] PR 已 MERGED，风险窗口真实关闭` exit 2，registry 保持 FINISHED 不复活。
- #1057 真跑全链路：`docker run -P` → `docker port` 回读 → pg_isready → 空库 alembic upgrade head + check_schema_sync 全过。
- #1058：`--self-test` 过 + `--check` 对 origin/main 文档树全绿（存量零行为变化）；DOC-MAP 存量三行 adr 链接行均带版本 token，新拦截不误伤存量。
- 门禁矩阵：ruff / compileall / gov-surface / ai-work / invariant-diff / immutability（base=origin/main）全过。
- pending：eslint / tsc / knip（worktree 无 node_modules，与本次全 python 改动无交集）由 PR CI `pr-typecheck`/`pr-compileall` 覆盖。

## Revisit

- #1045 的 git 全局选项表是白名单式（`_GIT_VALUE_OPTS`），git 新增带值全局选项时需同步；漏新选项的后果是漏拦（fail-open 向），与 hook 的 fail-closed 命中语义不冲突。
- #1047 docstring 状态机豁免仍开放：出现真实 docstring 误报事故时再评估（按注释行同款棘轮）。
- #1058 DOC-MAP「行在场但零 token」从静默改 BLOCK 后，未来提及性 adr 链接行（非索引行）若不带版本 token 会被拦——出现首次误伤时再评估是否拆分提及行与索引行。
- #1059 `_quote` 转义集扩至 `\n`/`\r`，`\t` 等其余控制字符仍会原样落盘（YAML 行结构不受影响，暂不扩）。
