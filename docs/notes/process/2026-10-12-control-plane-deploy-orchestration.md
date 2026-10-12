# control-plane-deploy 补端到端编排、激活闸门与前端构建坑

Status: implemented
Class: process

## Decision

「main 同步 + 前后端部署 + 服务重启 + 生产库迁移 + Agent 热更新」这一常见部署请求，由现有
`control-plane-deploy` skill 承载，不新建 skill。本次只补 SOP 没覆盖的部分：

- `.claude/skills/control-plane-deploy/SKILL.md`
  - 新增 §0.5 端到端编排：阶段 A 只读检查与构建（基线、共享检出状态、批次激活闸门查询、
    `DO-NOT-SWITCH.txt` 标记、新增迁移、scan / `--force` 判据、导出站点 env 后的守卫与就绪检查、
    同一提交的前端与 bundle 构建）→ 停顿点 → 阶段 B 先后端后前端 → 阶段 C canary 与批量热更新 → 报告项。
  - 授权不写进 skill：授权由 Owner 在每次部署请求中给出；skill 只规定「没有授权就停在阶段 A 第 14 步」，
    以及 Harness 拦截时不得换工具重试。
  - §1 步 2 补四条：`--out` 必须是新目录；`DO-NOT-SWITCH.txt` 约定；bundle 内前端是构建时线上副本；
    守卫 schema 检查回落仓根 `.env.backend`。
  - §1.5 补「共享检出不得跑 `build:prod`」警告，依赖安装由软链共享检出的 `node_modules` 改为 `npm ci`。
  - 头部状态句与 §7 追加行：新增条目标 `⚠️待校对`，按「校准来自实跑」的规矩等真实部署跑通后解除。
- `docs/operations/2026-08-29-post-review-deploy-runbook.md` 头部加过期指针：§1.3 的手动
  `alembic upgrade head` 与现行红线冲突，部署改按 skill 执行。

本次核对出的事实（均附查法，见 Verification）：

1. bundle 的 `frontend/dist-prod` 不是按目标提交构建的，而是复制构建源当时的同名目录，即共享检出里
   正在服务的前端。本机 nginx 不读 bundle 里这份，但站点安装 S2 会把它落到部署根并由站点 nginx 服务（#3694）。
2. 共享检出的 `frontend/dist-prod` 就是 nginx root；`build_bundle` 缺该目录时提示的
   `npm run build:prod` 在共享检出执行会清空线上目录。
3. `build_bundle` 用 `copytree(dirs_exist_ok=True)` 合并复制：往已存在的 rev 目录构建会留下上游已删除的文件，
   并计入摘要。
4. `check-deploy-readiness.py` 与 `check_alembic_at_head.py` 在环境变量缺失时回落到仓根 `.env.backend`。
   D6 之后那是 dev 配置，可与站点 env 漂移，检查目标因此不受站点配置约束。2026-10-12 核对时两份文件的
   `DATABASE_URL` 恰好相同，不导出时查的仍是生产库（#3693）。
5. unit 的 `ExecStartPre` 精确校验 alembic 在 head（不带 `--allow-behind`）：迁移执行后把 `current`
   指回旧 rev 会启动失败，跨迁移回滚不能只靠重指 `current`。
6. `GET /api/v1/hosts` 不带分页参数时仍按默认 `limit=50` 截断；fleet 超过 50 台时裸数组会漏主机。

## Alternatives

- **新建「一条龙部署」skill**：弃。与 `control-plane-deploy` 抢同一批触发词，调用被分流后容易出现零调用的空壳
  （2026-10-12 `skill_usage_report.py` 已报 4 个 HOLLOW），两份顺序与闸门也会各自漂移。
  `control-plane-deploy` 同日的用量是 Claude 调用 8 次、Codex 读取 21 个会话。
- **`.claude/commands/` 斜杠命令承载提示词模板**：弃。只有 Claude Code 识别；Codex 与 Cursor 经
  `.agents/skills` symlink 读同一份 skill，命令会打破多 Harness 共用一份 SOP 的现状。
- **把授权写进 skill**：弃。授权只对当次请求有效；auto 权限模式下的分类器也不认对话内授权。
- **把「seed 迁移停用版本 vs 在途 job 快照」（2026-09-13 记录）写成闸门**：弃。退役主路径已改为 scan +
  `backend/services/script_retirement.py`（60 天冷却、最新活跃版本豁免），改用通用的阶段 A 第 9 步
  （列出新增迁移并逐个读 docstring）。
- **在本 PR 修工具的 env 回落与 bundle 的前端语义**：不做，超出本次范围；SOP 给出正确的运行方式，
  工具层的修正另行立项（#3693、#3694）。

## Verification

- 前检：开放 PR 无一触及 `.claude/`、`.agents/` 或本 runbook；`ai_work.py status --risk` 在窗 LIVE 记录
  （`nx2-spflashtool-v6-flash`）的 scope 不含本次文件；已 declare。
- 只读实跑（2026-10-12，`BASE=fbb5ec61`，目标 `origin/main@bb4f2340`）：
  - 阶段 A 第 5 步批次查询输出 `#3653 OPEN 批次 B5：…`；
  - 第 7 步输出 `stp-releases/43dfc764/DO-NOT-SWITCH.txt` 一条（B5 U5 构建、未激活）；
  - 第 9、10 步无输出（无新增迁移，无 scan / `--force` 判据文件）；
  - 第 11 步导出站点 env 后：`check_alembic_at_head: OK —— alembic_version='b2c3d4e5f6a7' == head`，
    守卫 exit 0（监控资产 DRIFT 仅 WARN）；就绪检查 `[OK] automated checks passed`，exit 0。
- 前端事实 1 的证据（入口文件名取自各目录 `index.html`）：线上 `frontend/dist-prod` 为 `index-CuUVyo6q.js`，
  `index.html` mtime 为 2026-10-10 11:54:40；`fbb5ec61` 的 manifest 写于同日 11:52:04，其前端为
  `index-Cdmk6acJ.js`，与 `dist-prod.bak-20261010-fbb5` 相同；`43dfc764`（2026-10-11 构建）的前端为
  `index-CuUVyo6q.js`，`index.html` mtime 与线上同为 11:54:40。
- 代码核对：`tools/release/build_bundle.py`（`TREE_LAYOUT`、`_copy_tree`）、
  `backend/agent/contracts/artifact_digest.py`（`_CONTROL_PLANE_ROOT = "backend"`，跳过 `agent`）、
  `tools/dev/check-deploy-readiness.py`（`_database_url`）、`tools/dev/check_alembic_at_head.py`
  （`--allow-behind` 语义）、`systemctl cat stability-backend`（`ExecStartPre` 不带 `--allow-behind`）、
  `backend/api/routes/hosts.py`（`list_hosts` 默认 `limit=50`）、`backend/api/schemas/base.py`（`PaginatedResponse`）。
- 阶段 B、C 未实跑（会改变生产状态），相关条目标 `⚠️待校对`。
- 同日更正：首版把事实 1、4 写成「nginx 不读」「不导出就是对 dev 库给出通过结论」。随后只比较两份 env 的
  `DATABASE_URL` 是否相等（未打印取值），结果相同；`tools/site_config/stages.py` 的 `stage_s2_release_env`
  与 `deploy/control-plane/nginx/stability-platform.conf` 表明站点安装会服务 bundle 前端。两处已在后续 PR 中按实更正，
  skill §7 追加了更正行。
- 写作约定：新写与改动的句子按 `writing-conventions.md` §5 自查。
- 测试与门禁结果见 PR 描述。

## Revisit

- 下一次真实部署按 §0.5 跑通后，在 skill §7 追加校准行并解除 `⚠️待校对`；与现场不符处同 PR 修正。
- 如果 `build_bundle` 改为按目标提交构建前端，或 nginx root 改指发布根（#3694），修订 §1 步 2 补充与 §1.5。
- 如果两个检查工具不再回落到仓根 `.env.backend`（#3693），简化阶段 A 第 11 步前的警告。
- 如果批次 issue 标题前缀（「批次 B」）或单元 PR 的 `Refs #` 约定变化，同步修订阶段 A 第 5 步。
- fleet 超过 200 台时，阶段 C 第 6 步的分布统计改为分页累加。
