# ai_work --worktree 路径校验与修正通道（相对路径拼接错值）

Status: implemented
Class: bug-fix

## Decision

`tools/dev/ai_work.py` 三处改动：

1. **新增 `normalize_worktree(path, *, cwd=None)`**：只接受**绝对路径**，
   相对路径抛 `ValueError`（拒绝信息含「按 cwd 会解析成什么」）；
2. **`declare` 接入校验**：拒绝相对路径；目录不存在仅 **WARN 不拒**
   （finish 后 worktree 已删是合法场景，§5.2 档 2/3 本就覆盖）；
3. **`update --worktree`（新增参数）**：修正通道，顺带按新路径**重推导 `branch`**。

## 缺陷确认（实锤，非理论）

`ai_work.py` 原用 `os.path.abspath(args.worktree)` 写入记录。`abspath` 以 **cwd**
为基准——**在 worktree X 内传相对路径**即得 `X/<相对路径>` 的拼接值。

registry 中实测 **4 条**该形态错值：

```
703 第3面…              /home/…/.wt/stp-703b/.wt/stp-703b
fix-2358-2359-…         /home/…/.wt/stp-2316-adopt-unassigned/.wt/stp-2358-2359-frontend
fix-2381 dev bootstrap… /home/…/.wt/stp-2381-dev-bootstrap/.wt/stp-2381-dev-bootstrap
fix-2399 seed 迁移守卫… /home/…/.wt/stp-2399-seed-boot/.wt/stp-2399-seed-boot
```

**且仍有活跃实例**（本单开工当天产生）：一条 `CODING` 记录
`…/.wt/ci-backend-hang-fix/.wt/ci-backend-hang-fix`。

### 连带影响（比路径本身更严重）

`branch` 是**从 worktree 推导**的（`_git(["rev-parse","--abbrev-ref","HEAD"], worktree)`）。
实测坏路径上 git 返回 `[]` → `branch = None`：

| 环节 | 后果 |
|---|---|
| §5.2 档 1（worktree diff） | 路径不存在 → 不可用 |
| §5.2 档 2（branch diff） | `branch` 为空 → 不可用 |
| §5.2 档 3 | **静默降级到 `declared` 单独生效** |
| §3.4 issue 查重 | `extract_issue_numbers(..., branch or "")` **少一路启发式输入** |

实测 4 条错值记录中 **3 条 `branch=''`**，印证该链。

## 契约依据

- **§1.2**：`worktree` 是**已有字段** → 加 `update --worktree` **不违反字段封闭性**
  （§3.6 禁的是新增 `notes`/`plan` 类自由文本字段）；
- **§5.1**：「`update` 可覆写 `declared`（声明过期由执行侧显式清理）」→
  修正错误 `worktree` 是该原则的直接延伸；
- **§5.3**：`scope` **明确拒绝绝对路径**（repo-relative）——本单对 `worktree`
  取**方向相反**的同款纪律（只接受绝对路径），依据是二者消费方式不同：
  `scope` 是仓库内相对路径匹配，`worktree` 是 git 工作目录。

## Alternatives

- **自动把相对路径转绝对**（`os.path.abspath` 后就地接受）→ 否决：会**掩盖调用方的
  意图错误**（调用方以为指向 A、实际指向 B 时无从察觉），且无法给出可执行提示。
  本仓对 `scope` 的既有取向也是**显式拒绝**而非静默归一。
- **`declare` 时目录不存在即拒绝** → 否决：`finish` 后 worktree 常被删除，
  重新 declare 同一 worktree 名是合法场景；§5.2 档 2/3 本就覆盖 worktree 不在场。
  故只 WARN。
- **只加 `declare` 校验，不加修正通道** → 否决：存量 4 条错值将**永久无法修正**
  （`update` 此前只有 `--scope`/`--pr`），且字段不可重建（记录已存在，重 declare 会被
  同名录拒绝）。
- **修正时不动 `branch`** → 否决：`branch` 由 worktree 推导，路径修正后不重推导会
  残留空/错值，§5.2 档 2 仍不可用——等于只修了一半。

## Verification

- `python3 tools/dev/ai_work.py --self-test` → **通过**（新增 worktree 校验 5 项断言）；
- **红绿双向**：把 `normalize_worktree` 还原为「不校验、直接返回」→ self-test
  在 `相对路径应抛 ValueError` 处**失败**；恢复后通过；
- **拒绝行为实测**：`--worktree myworktree` → `[REFUSED]` + **exit 2**
  （拒绝信息含「按 cwd 解析会得到 /home/debian13/stp-wt/myworktree」）；
- **WARN 不拒实测**：`--worktree /tmp/does-not-exist-xyz` → `[WARN]` + 正常 declare；
- **`update --worktree` 端到端实测**：记录指向 `stp-wt`（branch=`fix/worktree-path-normalization`）
  → `update --worktree /home/debian13/stability-test-platform` →
  输出 `[OK] worktree 已修正 … → …；branch='refactor/1520-agent-reexport-trim'`，
  记录两字段**均已更新**（branch 随新路径重推导）；
- `ruff check tools/dev/ai_work.py` → All checks passed。

## ⚠️ 实施中的一次事故（如实记录）

测试 `update --worktree` 时，我用**通用 `yaml.safe_dump` 手写 registry** 注入坏值
以构造场景——但 `ai_work.py` 用的是**自定义行式 YAML 解析器**（`yaml_load`），
通用 dumper 的输出虽合法 YAML 却**不符合其格式**，导致工具判定 registry 损坏并
**隔离留证**（`registry.yaml.corrupt-*`），**共享 registry 一度对所有会话不可用**。

**处置与结果**：

1. 立即识别为自伤并定位根因（自定义解析器 vs 通用 dumper）；
2. 用 `yaml.safe_load`（可读）→ **`ai_work.yaml_dump`（工具自己的 dumper）** 重建，
   并在写入前以 `yaml_load` 回读自校验；
3. **完整性核对**：864 条记录，与隔离文件**逐条一致**（缺失 0 / 多余 0 /
   字段异常 0）；`status` 与 `--self-test` 均恢复正常；
4. 保留 1 份隔离文件作审计凭证，清理其余。

**教训**：registry 是**共享可变状态**，测试其写路径**必须走工具自身的 API**
（或复制到临时 registry），**不得用外部序列化器手写**——格式契约属工具私有。

## Revisit

- **存量 4 条错值记录未批量修正**：本单只提供修正通道（`update --worktree`），
  **未擅自改他人/在窗记录**。4 条中 3 条已 `MERGED`（出窗，影响仅为历史留痕），
  1 条 `CODING`（`ci-backend-hang-fix`）**仍在窗**——建议由该 Execution 自行
  `update --worktree <绝对路径>` 修正（或由 owner 裁决代为修正）。
- **`whoami` 路径**：`whoami` 的 `--worktree` 也是 `os.path.abspath`，但它只用于
  **匹配查询**、不写入记录，故未纳入本单；若将来它写入记录，需同样校验。
- **无 CI 门禁**：本类问题（相对路径 → 拼接错值）目前只由 `declare` 入口拒绝。
  若将来出现**绕过 CLI 直接改 registry** 的情形，需评估加一个 registry 结构校验门禁。
