# alembic revision 不可变门禁（#2258，承接 #2046）

Status: implemented
Class: process

## Decision

新增阻塞门禁 **`alembic-immutability`**：`backend/alembic/versions/*.py` 中**已合入 main**
的文件被改写/删除即红。

**为什么是二值违约**（而不是风格问题）：2026-09-13 06:20Z–07:25Z 的 rechain 窗口里，
已合入 main 的 revision 被反复改写 `down_revision`（6 次改写跨 3 个 revision，5 个是
seed 迁移）。对在该窗口执行过 `alembic upgrade` 的库，**新插入到其当前版本之前的 revision
永远不会执行**——而这类库的 `alembic_version` 仍是 head，`tools/dev/check_alembic_at_head.py`
的**等值**判定判绿：库缺陷与「已对齐」在护栏看来完全一样（#2046 已按只读核对确认本机生产库
恰好未命中，但这不改变盲区）。

**豁免（机械化，唯一）**：同一 diff 新增的 revision 文件中，有 `down_revision` 指向被改写
revision 的 id（字符串或元组均可）→ 该文件放行并打印 `[NOTICE]` 留痕。这正是 #1717 的先例
形态（为 `dd44ee55ff66` 补的 `f6a5b4c3d2e1`）。**删除/改名不豁免**——重放迁移补不了
「该 revision 不存在」这件事（停在其后的库会直接找不到它）。

**接线**：

- 工具 `tools/dev/check_alembic_revision_immutability.py`（`--base` / `--self-test`）；
- `scripts/run_gates.py` 的 `check:pr`（19 gates）、`check:full` 自动继承；
- `.github/workflows/ci.yml` 的 **lint job**（与 ADR-0020 的脚本版本不可变检查并列）——
  经既有 required check `lint` 生效，**无需改分支保护**；
- `tools/dev/check_governance_surface.py` 的 `GATE_TO_CI_ANCHOR` 登记（S5x 要求新门禁
  必须声明 CI 对应物——本 PR 第一版就是被这条守卫拦下的，补登记后才绿）。

**纪律落点**：`docs/development/dependencies-and-quality.md` 的「Lint 与本地门禁」一节
补了一条（rechain 只应在未发布 revision 上做；确需改写已合入者时同 PR 附重放迁移）。

## Alternatives

- **扩展现有 `check-script-version-immutability.py`**（我在 #2258 里的初版建议）：落地时
  改判为新工具——那个工具的规则、docstring 叙事与 `Violation` 形状都是 ADR-0020 脚本域
  专属，混入第二域会让两边的豁免判据互相干扰；共享的只是约 40 行 `git diff` 骨架，
  不值得为此合并。两者的 `--self-test` / `--base` 约定保持一致。
- **advisory（只告警不阻塞）**：漏跑是二值违约（种子行缺失 → 脚本不可用 / 准入
  `script_verify_failed`），与 ADR-0020 那条门禁的动机同型 → 选硬拦。若日后证明误报代价
  过高，把该 gate 从 `check:pr` 的 profile 里摘掉即可（工具与自证保留）。
- **自动识别「智能豁免」（例如按 diff 语义判断是否等价重排）**：不可机械判定，且任何
  「看起来等价」的改写都会让停在其后的库跳过一个执行点，否决。
- **改 `check_alembic_at_head.py` 扩面**：那条 gate 的职责是「库对齐 head」的部署探针，
  与「revision 文件不可变」是两件事，混在一起会让双方的红因难以区分，否决。

## Verification

worktree `.wt/stp-2258-alembic-immutability`，解释器 `/home/debian13/stability-test-platform/.venv/bin/python`：

```bash
python -m pytest tests/test_alembic_revision_immutability_gate.py -q   # 6 passed
python tools/dev/check_alembic_revision_immutability.py --self-test     # 分类/解析红绿双向
python tools/dev/check_alembic_revision_immutability.py --base origin/main  # [OK] 无变更
python scripts/run_gates.py check:quick    # [OK] 10 gates
python scripts/run_gates.py check:pr       # [OK] 19 gates（含新 gate）
```

- 单测在**真实临时 git 仓库**里构造历史（与 ADR-0020 门禁测试同法）：改写已合入 revision
  → 红；改写 + 重放迁移 → 绿且留痕；删除 → 红（即便有重放也不豁免）；正常新增 → 绿；
  非 versions 路径的改动 → 不受影响；`--self-test` 绿。
- **真实历史反例**：`--base e39b1fef~1`（09-13 rechain 窗口里的改写提交）对当前 HEAD
  运行 → 门禁精确报出被改写的 revision 并给出重放迁移指引——即这条门禁若当年存在，
  会把那次窗口拦在合入前。
- 自证也抓到了实现缺陷：初版把「删除/改名」也算进豁免，被 `--self-test` 的删除用例照出来
  （已把豁免收窄为仅 `M`）。

## Revisit

- **已知漏判形态**：若某 PR 既新增一个无关的 child revision、又改写一个更早的 revision，
  且该 child 的 `down_revision` 恰好指向被改写者，则会被误判豁免。发生时应把豁免判据升级为
  显式哨兵（例如要求新增文件的 docstring 标注 `replay-of: <revision_id>`），而不是继续放宽
  或收紧启发式。
- **误报代价**：若真实的 rechain 需求频繁到阻塞日常合入，优先复核「是否本该在未发布阶段
  rechain」，其次再考虑把 gate 降为 `check:full` 级（不阻塞合入）。
- 本门禁只覆盖 **Agent 侧 git 仓的 revision 文件**；「库是否漏跑」仍需 `schema-at-head` 之外
  的运行期核对（#2046 的只读核对动作可作为他机排查模板）。
