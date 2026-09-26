# check_script_packages --publish：原子落位 + 只增不改 + 先判等价后发布（2026-09-26）

Status: implemented
Class: bug-fix

## Decision

发布 ADR-0051 D7 工具包前核对站点发布路径，`check_script_packages --publish` 三个缺陷：

1. **非原子**：`rebuild_all(out_dir=站点)` 把包**边压缩边写**进 `packages/{name}/{version}.tar.gz`——
   整个构建期间文件处于半截状态，此刻拉包的 Agent 读到即 sha 不符（strict 下步骤 exit 2）；
2. **先写后判**：写入发生在 `check()` 之前——族树改了没发版时，会用新字节覆写**已发布**版本的文件名（违反只增不改）；
3. **全量重写**：每次发布把 35 族最新包全部重写一遍（即使字节相同），放大 1 的竞态窗口。

修复：`rebuild_all` 一律构建到构建目录；新增 `publish_latest`，仅在 `check()` 全绿后执行——构建 sha 必须等于登记、
站点已有同名包时同字节跳过（不重写）/ 不同字节拒绝、缺失的包先写同目录临时文件再 `os.replace` 原子落位。
与 #3369 给 `package_tool_asset` 加的发布守卫同一口径。

## Alternatives

- **只在 SOP 里写「从干净 main 发布」**：规避 2 但治不了 1/3，且仍是操作者自觉级守卫；弃。
- **发布到临时目录再整目录 rename**：站点根还有其它族与工具包，整目录替换会波及无关条目；按文件原子替换足够。

## Verification

- self-test 发布段改写：只落最新版、无临时文件残留、同字节重发不改 mtime、站点同名不同字节拒绝且不改动、构建 sha≠登记拒绝
- `tests/test_adr0051_phase2a.py` 新增 2 例 + 旧例迁新 API → 18 passed；**变异自证**：同字节也重写 → 红、不同字节照样覆写 → 红、
  不核构建 sha → 红（各 1 例）
- **真实数据演练**（站点包根的本地副本，46MB，未碰站点）：`--publish` 结果「新落位 1（powercycle_setup@1.2.6）、同字节跳过 34」，
  1.2.6 sha == 登记、无临时文件——同时证明站点现有 34 个最新包与 main 族树逐字节一致
- `check:quick` 见 PR

## Revisit

- 本单合入后，#3223 的 `powercycle_setup 1.2.6`（已登记未发布，#3223 评论）与 #3378 的四个 D7 消费族新版本即可用
  `--publish` 安全补发（只会新落位缺失包）。
