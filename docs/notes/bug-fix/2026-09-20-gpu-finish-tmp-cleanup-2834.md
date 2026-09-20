# gpu_finish 回收自建临时结果目录：宿主 /tmp（tmpfs）不再随 GPU 窗单调累积（#2834）

Status: implemented
Class: bug-fix

## Decision

**根修点选在「谁建的谁回收」，而不是在 `_run` 里补一句 `rmtree`。**
v1.0.0–v1.0.5 的 `local = Path(tempfile.mkdtemp(prefix="gpu-results-")) / "test_log.txt"`
（`gpu_finish/v1.0.5/gpu_finish.py:62`）建完就不管，宿主 `/tmp` 是 tmpfs——按 RAM 计，
且**没有回落路径**。实测（#2834）：`.68` 打满 100%（1.8G/1.8G，剩 1.4M）已把该机
agent 热更新撞成 ENOSPC 失败（`hot_update_remote_failed`，`Wrote only 4096 of 9728 bytes`），
`.64`/`.67` 各钉住 3.3G/3.6G。

实现三件（都在入口脚本，不新增 env 键）：

1. `_PULL_TMP_PREFIX` + 模块级 `_TEMP_RESULT_DIRS`：**建目录的唯一入口是
   `_mk_result_tmpdir()`**，建即登记；拉取点改为 `local = _mk_result_tmpdir() / "test_log.txt"`；
2. `_discard_result_tmpdirs()` 挂在 `main()` 的 **`finally`**——不是成功分支。
   GPU 窗失败率不低（ENOSPC / 离线 / pull 超时都抛），**失败路径才是把目录留下的那一路**；
   `sys.exit(1)` 抛的 `SystemExit` 同样经过 `finally`；
3. 删除带**形状判据**：只删「`gettempdir()` 的直接子项 + 以 `_PULL_TMP_PREFIX` 开头」的目录，
   不匹配就跳过并往 stderr 记 `skip tmp cleanup (unexpected shape)`。脚本手里有 `shutil`
   权限，少这道判据时一次变量名写错就是删别人的目录——**跨进程数据破坏比留几个临时文件
   严重一个量级**，所以「宁可留、不可错删」。stderr 行由引擎 reader B 丢弃，不污染
   stdout 的 JSON 结果契约。

**为什么只发一族版本**（本单的取舍，写给 owner）：同族 `powercycle_finish/v1.0.4:90`
（`prefix="powercycle-results-"`）与 `sleep_finish/v1.0.2:55`（`prefix="sleep-results-"`）
是同一形状，但正文实测 `powercycle-results-*` 144–194 个目录**总量 <1MB**，`sleep-results-*`
未测到量级。而 ADR-0029 要求每版全量副本——为 <1MB 各开一棵新树，付的正是 #735 记的
膨胀成本（脚本目录占后端 ~27%）。所以本单**修 GB 级且已造成生产事故的那一族**，另两族
只带 `file:line` 评注，等它们下一次本就要做的 bump 顺带收（helper 形状完全一致，搬运是机械的）。

## Alternatives

- **只改 `finally` 里补一句 `rmtree(local.parent)`**：否决。`_run` 有多条 return 路径与
  异常路径，逐点补必然漏；登记 + `main()` 出口统一回收才能覆盖「失败窗」。
- **改成固定路径 + 先清后建**（正文提到的另一选项）：否决。同 host 多设备并行时
  两个进程会互相删对方的结果（`run_id` 里加设备维度正是为防这种碰撞，见 v1.0.2），
  而且固定路径让「谁留下的」重新变成不可判定。
- **Agent 侧加一个 `/tmp` 清扫器**（一处修三族）：不在本单。它是「自动删托管机上
  符合前缀的目录」的**策略**变更（要 mtime 判据、要防误删别的 harness 的东西、还会与
  正文建议 2 的存量清理重叠），该由 owner 裁；本单只让脚本不再产生新垃圾。
- **顺手把 `/tmp` 水位纳入观测**（正文建议 3）：查完再记，不在本单做——控制面本机
  `/tmp` **已在采**（`node_filesystem_size_bytes{mountpoint="/tmp",fstype="tmpfs"}`，
  运行中 9100 端点实测）；缺的是 **agent fleet 主机**：中心 Prometheus 只有
  `stability-backend`（127.0.0.1:8000 /metrics-internal）与 `file-server`（127.0.0.1:9100）
  两个 job，**不抓 agent 主机的 node-exporter** ⇒ fleet 侧 `/tmp` 无任何抓取面，
  「打满才发现」是结构性的。这是独立一条观测缺口，别塞进 bug 修复里顺手做。

## Verification

- `pytest backend/agent/tests/test_gpu_finish_tmp_cleanup_2834.py -q` → **6 passed**：
  登记→回收整树；**拉取点必须走登记路径**（否则清单永远为空）；`main()` 成功路径回收；
  `main()` **失败路径**回收（`SystemExit(1)` + `output_result(False)` 之后目录仍不在）；
  形状判据三例（外部目录 / 嵌套目录不删 + stderr 有声音 + 正确形状照删）；建删共用常量
- `pytest tests/test_gpu_finish_tmp_cleanup_guard_2834.py -q` → **4 passed**（源扫描按 #2639
  用 `SourceGuard`；判据绑**代码形状** `Path(tempfile.mkdtemp(prefix="`——绑散字符串会被
  本文件的版本说明自我误伤，那正是 #2641/#2642 记过的形态）；
  **变异自证**：把创建点改回旧形态，判据必须认得（`LEAK_SHAPE in poisoned` 双向断言）
- `pytest backend/agent/tests/ -q` → **2238 passed**；`pytest tests/ -q` → **1617 passed**
- `tools/dev/check-script-version-immutability.py --base origin/main` → OK（只新增
  `v1.0.6/`，已发布目录零改动）；`check_governance_surface --check` → S1–S15 全绿；`ruff` 通过
- **pending（不当作通过）**：① `POST /scripts/scan` 注册 v1.0.6 + 把 5 条引用 v1.0.5 的
  `plan_step` 重指过去——都是控制面写操作，须运维授权（生产 `script` 表实测：
  `gpu_finish 1.0.5 is_active=True`、`plan_step` 引用 5 处）；② 存量清理
  （`find /tmp -maxdepth 1 -name 'gpu-results-*' -mtime +1 -exec rm -rf {} +` 扫 48 台
  agent 主机）按正文属**止血**，同样需 owner 批准，本单未执行任何远端写；
  ③ 下一 GPU 窗后 `.68` 的 `/tmp` 水位是否停止上涨。

## Revisit

- 另两族（`powercycle_finish`、`sleep_finish`）的同形状泄漏仍在，等各自下一次 bump 顺带收；
  若 owner 决定批量收，helper 与判据可直接照搬本单（三个文件同构）。
- fleet 侧 `/tmp` 抓取面缺失是独立缺口：要么给 agent 主机装 node-exporter 并纳入中心抓取，
  要么在 agent 心跳里带宿主磁盘水位（与 #2757 的设备侧磁盘是不同对象）。要立单请裁。
- 如果将来出现「脚本需要跨步骤保留拉回文件」的用法，本单的 `main()` 出口回收点就要重新
  设计——那时正确做法是把结果搬进中心存储再清，而不是放宽判据。
