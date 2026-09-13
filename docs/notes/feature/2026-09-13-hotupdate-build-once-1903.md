# 热更新 P0 过渡：批量整批只构建一次 tarball + 压缩级 9→6（#1903 / ADR-0040 §5.1）

Status: implemented
Class: feature

## Decision

按 [ADR-0040](../../adr/ADR-0040-deployment-artifact-digest-protocol.md) §5.1 的 **P0 过渡项**落地两项**不改协议**的优化：

1. **压缩级 9 → 6**（`_TARBALL_COMPRESSLEVEL = 6`）：控制面实测 252MB 源树打包
   16.6s → 6.4s；体积 125.7MB → 126.0MB（+0.3MB，内网代价可忽略）。
2. **整批只构建一次**：`execute_hot_update(tarball=...)` 接受预构建载荷；
   `batch_hot_update.py --direct` 在循环外构建一次并复用到每台。
   此前每台重复构建（`--direct` 循环内 → 48 台 ≈ 13 分钟纯 CPU，且控制面同机是生产 DB 宿主）。

预期：单台 `execute_hot_update` p50 从 ~21s 降至 **~5s**（打包分摊 0.13s/台 + 传输/远端/重启不变）。

## Alternatives

- **隐式缓存（按 mtime/大小指纹）**：能顺带惠及 UI/API 单台路径，但 rsync 类同步会保留
  mtime，存在陈旧风险；且 P1 的 digest 才是正确的缓存键。→ 否决，隐式缓存留给 P1。
- **压缩级 1**：打包 3.2s、体积 132MB——再省 3s 但 +6MB；收益边际，且与本 ADR 已
  记录的 6 不一致。→ 暂不采用，若 P1 后仍有收益再评估。
- **只改批量、不动压缩级**：省 ~16s/台但 UI/API 单台路径仍为 20s。→ 两者一起做，成本相同。

## Verification

- `python -m pytest backend/tests/services/test_host_updater.py -q` → **20 passed**
  （新增 3 项：默认压缩级与可覆盖、预构建载荷不重复构建、`--direct` 整批构建一次并向下传递）
- 邻近回归：`test_precheck_sync.py` + `test_upgrade_gate_api.py` + `test_plan_run_abort_api.py`
  → **42 passed**
- `python scripts/run_gates.py check:quick` → 见 PR（check:quick 7 gates）
- **pending**：真实 fleet run 的 `duration_ms` 复核（下次批量热更新时以日志 SUMMARY 对照
  p50 ≤10s 目标）；本单未跑真机热更新，不做超范围声明。

## Revisit

- 下次 fleet run 若 p50 未降到 ≤10s，先看 per-phase 归因（控制面打包剩余占比 vs 传输/远端）；
- ADR-0040 进 P1 后，本项的两处改动由 digest 缓存键与 no-op 判定取代（`_TARBALL_COMPRESSLEVEL`
  保留即可，构建次数由 digest 决定）；
- ADR-0040 被否决/rework 时，本项仍是独立的纯优化，无需回滚。
