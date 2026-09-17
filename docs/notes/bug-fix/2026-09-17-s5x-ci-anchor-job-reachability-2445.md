# #2445 S5x 锚点改按「真实 job 的真实 step name + PR 事件可达性」断言

Status: implemented
Class: bug-fix

## Decision

`tools/dev/check_governance_surface.py` 的 S5x 原来只断言 `anchor in workflows[wf]` ——
**workflow 全文子串**。它既不区分 job、也不区分事件，注释与 `run:` 命令体里的同名词同样算命中。
`ci.yml` 里夜间 job（`backend-test` / `frontend-check`，`if: event_name != pull_request`）与
PR job（`pr-agent-tests` / `pr-compileall` / `pr-typecheck` 等）有大量同名 step，于是
**删掉 PR 路径那一步 S5x 仍然全绿**：门禁登记退化成「这个名字在文件里出现过」的空断言。
改为三条判据：

1. **锚点必须是 step name**：新增约 30 行结构扫描器（`jobs:` → job id → `if:` → `steps:` →
   六空格缩进的 `- name:`），只比对解析出的 step name 集合；注释行、`run:` 命令体、别的
   workflow 里的同名词一律不算。标量取值按 YAML 语义处理：引号内原样，无引号时剥掉
   「空白 + 井号」之后的行尾注释——否则一个名字后面挂行尾注释会凭空造出假锚点。
2. **登记表支持 pin job**：值形态扩为 `(wf, step)` 或 `(wf, job, step)`（`_s5x_anchor_spec`）。
   `frontend-build` 因此改为 `("ci.yml", "frontend-check", "Build")`：那一步的 name 就叫
   `Build`，`npm run build` 只出现在命令体里，旧判据把这件事糊了过去；不 pin job 时
   `Build` 还会同时命中 `docker-build` 的 `Build backend image`。
3. **合入前门禁必须 PR 可达**（`_s5x_pr_reachable`）：`check:quick` / `check:pr` 成员（名单从
   `run_gates.py` 文本取）的锚点，所在 job 至少要有一个在 `pull_request` 事件可达——无 `if:`
   视为可达，`== pull_request` 可达，`!= pull_request` 不可达，其它形态判 None（不可证明）
   而**不作数**。取名单失败、名单为空、或读到非 GATES 成员一律报红：可达性判据的地基本身
   不许静默收缩成空集，那会把整套判据变成假绿——正是本单要消灭的形态。

失真方向被刻意选成「只会少读、不会凭空多读」：扫描器少读一个 job/step 的后果是锚点消失
（响红灯），不存在把不存在的检查判成存在的路径。`jobs:` 解析不出任何 job 时同样必须响，
但只对登记了锚点的 workflow 断言，避免顺带打红无关文件。

## Alternatives

- **引 PyYAML 解析 workflow**：否决。本工具跑在 CI 的 `lint` job 里，那个 job 的 python 侧只
  `pip install ruff`；给治理门禁挂第三方依赖，等于把 required check 押在一条安装命令上，
  比 30 行定向扫描更脆。代价（结构假设写死在代码里）由解析器回归夹具钉住。
- **保留全文子串、只补「PR 可达」一条**：否决。命令体与注释里的同名词仍是假锚点，
  `npm run build` 这类登记继续成立，修不彻底。
- **解析器自守改成「`- name:` 行数 == 解析出的 step 数」交叉核对**：先写后删。它在本次真实
  缺陷（空行截断 `jobs:` 块）上确实响了红灯，但对合法形态会误红——YAML 允许块序列与父键同
  缩进，`matrix:` 下六空格缩进的 `- name: <entry>` 会被计数器当成 step；而任何少读都已表现为
  「锚点消失」的响红灯，这条核对不额外挡住假绿。留下的自守是：无 job 必红、名单解析失败必红，
  以及三条直接调用 `_s5x_parse_jobs` 的回归断言。
- **给全部锚点 pin job**：否决。登记表里只有 `Build` 需要 pin，其余 step name 本身足够具体；
  全量 pin 会把判据绑死在 job id 上，夜间 job 改名就得跟着改登记，收益不成比例。

## Verification

- `python tools/dev/check_governance_surface.py --self-test` → `[OK] self-test 通过`。S5x
  夹具从「把锚点串成一行字符串」重写为**合成工作流**（按登记表生成 job + `if:` + steps），
  红绿样例：配对齐 → 绿；step 被删 → 红；锚点掉进注释 / `run` 命令体 → 红；合入前锚点只在
  PR 不可达 job → 红；同锚点补一个 PR 可达 job → 绿；锚点藏在 step name 行尾注释里 → 红；
  step 整步被注释掉（词仍在文件里）→ 红；`PROFILES` 取不到成员 / 成员含未知门禁 → 红；
  未登记锚点的 workflow → 不误伤；另有 `_s5x_parse_jobs` 三条（空行与第 0 列注释不吞 job、
  行尾注释不进名字、第 0 列键关闭 jobs 块）与 `_s5x_pr_reachable` 六种形态表（含
  `pull_request_target` 不得判为可达）。
- `python tools/dev/check_governance_surface.py --check` → `[OK] 阻塞项全绿：S1–S14、S5x`。
  main 的真实 `ci.yml` 20 条合入前锚点全部在 PR 可达 job 命中，判据无需放宽。
- **变异自证（四条，逐一实跑且 on-target）**：`ci.yml` 先备份到 `/tmp`、`shutil.copy` 还原，
  还原后 `git diff -- .github/workflows/ci.yml` 为空。
  1. 删 `pr-agent-tests` 的 `Collect agent tests in clean env` 整步 → 1 条红，正是
     `agent-tests-collect`；
  2. 把 `内网主机地址检查(public 仓库)` 整步改成注释（锚点词**仍在文件里**，旧判据此时全绿）
     → 1 条红，正是 `ip-leak`；
  3. `pr-compileall` 的 `if:` 改为 `!= pull_request`（step 名不动）→ 1 条红，正是 `compileall`
     的可达性判据；
  4. `Execution Registry 自测` 改名并把原词挪进行尾注释 → 1 条红，正是 `ai-work`。
- 首轮实现自证了自测的必要性：扫描器把空行当成第 0 列顶层键，`jobs:` 块被第一个空行截断，
  `--check` 报出 23 条假红（只解析出 `backend-test` 一个 job）。修法是「空行与注释不改变块
  归属」并让 job 键容忍行尾注释；对应回归断言已进 `--self-test`。
- `python -m ruff check tools/dev/check_governance_surface.py` → All checks passed。
- `python scripts/run_gates.py check:quick` → 10 gates 全绿（前端三项需
  `frontend/node_modules`，本地以软链提供后跑）。

## Revisit

- `vitest` / `frontend-build` / `docker-build` / `integration` 今天不是合入前门禁，锚点落在
  PR 不可达的夜间 job 上合法。**若将来把它们前移进 `check:pr`**（#1569 那类前移），S5x 会
  立刻要求把对应 step 也搬进 PR 可达 job——届时先改 `ci.yml` 再改登记表，不要放宽判据。
- 扫描器只认「与 `steps:` 键同缩进的六空格 `- name:`」，本仓 6 个 workflow 100% 如此。若引入
  流式写法、用 `uses:` 复用 workflow 承载检查，或改变块序列缩进风格，需要扩扫描器；当前失败
  方向是响红灯（锚点消失），不会静默变绿。
- `docs/design/2026-08-governance-surface-protection.md` 的 S 规则表本就没有 S5x 行（只列
  S1–S11），本次不扩表；判据的权威描述在脚本头部与本 note。若 S5x 再扩面（例如断言 step 的
  命令体与 gate 命令一致），应按 ADR 裁决而不是继续加子串判据。
