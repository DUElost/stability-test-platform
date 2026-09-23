# check_script_packages 对老 checkout 纯 pycache 版本目录残壳的容忍（2026-09-23）

Status: implemented
Class: bug-fix

## Decision

Phase 3 合入后，**任何旧 checkout**（含生产主检出）`git pull` 只删跟踪文件——
210 个版本目录留下纯 `__pycache__/*.pyc` 空壳（git 视为干净，工具看不见差异）。
`check_script_packages.py --check` 的 `stray_version_dirs` 按目录存在判 stray，
当场 210 项假红（本机 publish 时实际撞上）。

修复：stray 判定改为「版本目录含**非 ignored 残渣**的真实文件才红」；
`__pycache__` / `*.pyc|*.pyo` / `.DS_Store` 组成的残壳容忍（与打包器排除面同语义）。
残壳本体不物理删除——它是 git 视野外的机器残渣，容忍判据即正确行为；
新鲜 checkout 行为不变（无残壳可容）。含真实文件的 stray（未迁干净的旧脚本）仍必红。

## Alternatives

- **教每个人 `git clean -fdx` 清残壳**：弃——CI 不会红但每个旧站点/每个升级 checkout
  的第一次 publish/scan 都会红，且 `git clean -fdx` 在生产根上是危险动作；工具判据
  应认识「ignored ≠ content」。
- **stray 判定读 `git check-ignore`**：弃——210×walk 的 git 子进程开销，且判据依赖本地
  gitignore 演化；用与打包器一致的显式排除集更稳。

## Verification

- `check_script_packages --self-test` 红绿双向更新（残留用例改为「真实文件红 + 纯 pycache 壳绿」双断言）；
- `tests/test_adr0051_phase2a.py` 16 passed（同双断言镜像）；
- **真实树验证**：修复后的工具指向主检出（现场 210 个纯 pycache 残壳）→ 等价检查绿；
  本 worktree（新鲜，无残壳）同样绿；
- `check:quick` → 15 gates OK；ruff OK。

## Revisit

- 若未来 `stray` 还要拦「ignored 但可疑」的残留（如编译产物目录），应升级为按
  `git ls-files` 对照而非文件名集合；暂无需求。
