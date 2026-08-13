# 空行注入污染：检测工具与三重防线
Status: implemented
Class: bug-fix

## Decision

编辑器插件会逐行插空行，一次污染后每次 diff 都虚胖一倍（历史上
`PlanEditPage.tsx` 三次被污染撞坏 diff）。三重防线：

1. 检测/清理工具 `tools/dev/collapse-blank-pollution.py`（`--check` 只报；
   按文件整体空行率判定，只动空行，AST 比对保证语义不变）；
2. CI lint job 阻塞式门禁（py/ts/tsx/js/jsx 全扫）；
3. 本地 pre-commit 钩子（`.githooks/`，一次性启用
   `git config core.hooksPath .githooks`）。

## Alternatives

- 只靠本地钩子：拒绝。钩子需开发者手动启用，当年 `pipeline_engine.py`
  就是这样漏进 CI 的。
- 只靠 CI 门禁：部分保留（钩子提前拦截，省一轮 CI）。

## Verification

- CI：ci.yml §lint「空行注入污染检查」步骤；
- 本地：`python scripts/run_gates.py check:pr`（pollution gate）。

## Revisit

若上游编辑器插件修复逐行插空行行为，可评估摘除门禁（保留清理工具）。
