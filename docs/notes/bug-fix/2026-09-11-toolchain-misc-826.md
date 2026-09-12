# 工具链杂项批：.dockerignore 与 pg_restore 静默退出（#826）

Status: implemented
Class: bug-fix

## Decision

批内 3 处逐项实测后处置：

1. **`.dockerignore` 漏点前缀目录（已修）**：只有 `venv*/`（不匹配
   `.venv`）——本仓约定用根 `.venv`，`docker build` 时数百 MB 进 context、
   `COPY backend/` 可能连虚拟环境一并打进镜像层。追加 `.venv/`、
   `.venv*/`、`.pytest_cache/`。
2. **pre-commit pathspec「不跨目录」为误报（不改）**：issue 断言无
   `:(glob)` 时 `*` 不跨目录。实测反证：`git ls-files --
   'backend/agent/scripts/*/mtbf_setup.py'` **匹配**了
   `backend/agent/scripts/mtbf_setup/v1.0.0/mtbf_setup.py`——git 默认
   pathspec（fnmatch 无 FNM_PATHNAME）的 `*` 跨 `/`，旧 pattern
   `backend/agent/scripts/*/v*/*` 对深层 `v*/sub/x.py` 同样命中。保留原
   实现，零改动。
3. **pg_restore_test.sh 无备份时静默退出（已修）**：`BACKUP_FILE=$(ls ...
   2>/dev/null | head -1)` 在 `set -euo pipefail` 下赋值语句直接非零退出
   ——下方「No backup files found」报错永远打不出。补 `|| true` 走空值
   分支。

## Alternatives

- **pre-commit 也改成 `:(glob)`**——放弃：改动会收窄既有语义（`*` 跨目录
  本就覆盖全部版本目录），无收益且增加 pathspec magic 理解面；issue 该
  项按误报不修（如实记录实测证据）。

## Verification

- 第 2 处反证：`git ls-files` 实测（上述命令）证明 `*` 跨目录；另以
  `git diff --cached --diff-filter=MD` 对 staged 版本文件跑新旧 pattern，
  输出一致（均命中）；
- 第 3 处前后对比：`BACKUP_DIR=/tmp/empty-backups-826 bash
  scripts/pg_restore_test.sh`——修复版输出 `ERROR: No backup files found`
  且 **exit=1**；回退版无任何输出（静默退出）；
- 第 1 处：`.dockerignore` 增行静态复核（dockerignore 语法为简单 glob，
  `.venv/`/`.venv*/`/`.pytest_cache/` 均命中所列目录）；
- test_impact=none（工具链配置与脚本，不进运行时代码路径）；
- `check:quick`：见 PR。

## Revisit

- 第 2 处若未来 git pathspec 语义变化（或引入 `:(glob)` 全仓约定），再
  评估 pre-commit 与 CI 门禁的 pattern 对齐；当前 CI
  `check-script-version-immutability.py` 的 SCRIPT_ROOT 前缀 diff 是权威
  兜底。
