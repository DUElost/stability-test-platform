# monkey_teardown v1.0.3：cleanup_paths 逐项 shell 引号化（#2285 第 6 项）

Status: implemented
Class: bug-fix

## Decision

发 **v1.0.3**（全量副本，`monkey_teardown.py` + `_adb.py`），改一处：

```python
# v1.0.2：quoted = " ".join(paths)          ← 计划作者提供的路径直接拼进设备侧 shell
quoted = " ".join(shlex.quote(str(p)) for p in paths)
```

该 `quoted` 同时用于 `rm -rf {quoted}` 与回读探针 `for p in {quoted}; do …`，故两处
一次收口。v1.0.2 **不动**（ADR-0020 已发布版本不可变，`content_sha256` 是扫描时冻结的
期望值）。

守卫：新增 `backend/agent/tests/test_script_version_fork_guards_2285.py`，按 #2048 的
做法**动态解析最新版本目录**断言（既有 `test_teardown_cleanup_894.py` 钉的是 v1.0.2，
新版本一出现就无人覆盖——#2048 的教训原文）。断言是**语义级**的：设备 shell 解析出的
argv（`shlex.split(rm_cmd)[2:]`）必须与原始路径列表逐项一致。

## Alternatives

- **A. 白名单正则拒绝（issue 建议的另一选项，如 `^/[/A-Za-z0-9._-]*$`）**：不选。
  验收要求「**含空格路径下仍正确**」——白名单会把含空格路径直接拒掉，等于把「注入」
  换成「清理静默漏项」（teardown 漏删恰恰是本脚本 v1.0.2 要消灭的形态）。`shlex.quote`
  同时满足「空格仍正确」与「无注入面」。
- **B. 原地改 v1.0.2**：禁止。已发布版本不可变（ADR-0020 + immutability 门禁）。
- **C. 新版本里对非法路径 fail-fast**：不做。该参数由计划作者提供，失败会让 teardown
  整个中断（设备留残留物）；引号化后非法字符只是**字面量**，无需拒绝。
- **D. 顺手扫其它脚本的同类面**：不做（超出本单；已记 Revisit）。

## Verification

- **差分实证（v1.0.2 vs v1.0.3）**：同一组路径
  `["/data/local/tmp/aim", "/data/local/tmp/a b", "/data/local/tmp/x;rm -rf /sdcard", "/data/local/tmp/q'uote"]`
  - v1.0.2 生成：`rm -rf /data/local/tmp/aim /data/local/tmp/a b /data/local/tmp/x;rm -rf /sdcard /data/local/tmp/q'uote`
    —— `shlex.split` 直接报 **No closing quotation**，且 `;rm -rf /sdcard` 是设备侧
    **第二条命令**（注入面实证）；空格路径被拆成两项（清不掉且会去删 `/data/local/tmp/a`）。
  - v1.0.3 生成：逐项 `shlex.quote`，`shlex.split(rm_cmd)[2:] == paths` 成立（空格、
    分号、单引号都不改变分段）。
- 新守卫用例通过；v1.0.2 的既有用例（`test_teardown_cleanup_894.py`）**18 passed**
  （未受影响的对照）。
- `python tools/dev/check-script-version-immutability.py --base origin/main`
  → `OK：backend/agent/scripts 下没有已发布版本目录被原地改动`。
- `ruff` 全绿。
- **未跑**：真机 teardown 端到端（需设备在线）；设备端 shell 的实际解析只在本机按
  POSIX 语义验证（`adb shell <cmd>` 由设备 shell 解析，与 `sh -c` 同口径）。
- **未做**：DB 注册。本 PR 只落地版本目录，**注册/激活走部署侧** `POST /scripts/scan`
  （`docs/development/script-versioning.md` §A.4）；本单不改任何 Plan 的版本 pin。

## Revisit

- **默认 pin 未变**：新版本目录存在 ≠ 有 Plan 用它。要让某个 Plan 用 v1.0.3，需要
  scan 之后重指 `plan_step`（或脚本版本参数）——属部署/计划侧动作，本单不做，避免
  「发版本顺带改计划」的隐式行为。
- **同类面未扫**：其它设备脚本是否也有「计划作者输入直接拼进设备侧 shell」的形态，
  本次审计未覆盖。建议按同一判据扫一遍（`adb_shell_quiet` / `adb shell` 的 f-string
  拼接点），发现即按本单模式发新版本。
- **fork 守卫的覆盖面**：本文件只盯 `cleanup_paths` 一处引号化。若 v1.0.4 出现别的
  注入面（如 `process_names` 拼进 `kill`），需在同一文件里补断言。
