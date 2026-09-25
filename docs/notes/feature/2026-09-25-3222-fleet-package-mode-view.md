# #3222：fleet 包模式持续可核验——package_active 从 ack 提升为 host 级不变量（2026-09-25）

Status: implemented
Class: feature

## Decision

ADR-0051 的「fleet 全 strict」此前只能靠实操记录背书（#3222 F06.1：判据在 verify ack 里，
不落账不聚合）。本单把它变成机器不变量：

1. **host 显式列 `script_packages_mode`**（`package|tree|mixed|NULL=unknown`；
   禁 extra 裸键，先例 ADR-0040 D2）+ 迁移 `c7d2e5f8a1b3`。
2. **推导在 presence sweep 内**：`derive_packages_mode(verify_entries)` 只看**带包身份**
   （expected 行含 `package_sha256`）的逐条 ack——全 True=package / 全 False=tree /
   部分=mixed / 无带包身份结果或 RPC 不可用=**unknown（未知不是绿）**。
   `_persist_modes` 每轮写列，变化才 UPDATE。
3. **两个出口**：Prometheus `stability_host_script_packages_mode{mode}`（sweep 聚合 set）
   + `GET /script-presence/summary` 新增 `fleet_packages` 四计数（退役 host 不计）。
   机器不变量 = `{mode="package"}` 等于非退役 host 数且 `tree/mixed` 为 0。
4. **附带收口 #3262 遗留小项**：sync 对 retired 且已存在的行（seed 历史行无包身份）
   一轮按包回填 `package_sha256`+内容字段（此后不再读包）——登记过的条目在 catalog 里无死角。

## Alternatives

- **每行 presence 记 mode**：弃——包模式是 host 级属性（agent 代码 + env），per-(name,version)
  记 109 倍冗余；聚合仍要折回 host。
- **heartbeat 上报模式**：可行但动 agent 契约与心跳 schema 两层；sweep 已经在收 verify ack，
  判据零新通道。
- **告警规则一并加**：本单只建「可核验」；`tree/mixed > 0` 的告警阈值（如持续 30m）等 fleet
  全量过一轮新载荷后单独加，避免部署窗口天天误响。

## Verification

- `test_script_packages_mode_3222.py`：derive 七分支（含「无包身份行不误报 package」）+
  fleet 聚合（退役不计、NULL→unknown）；sync 回填新例（retired 行补身份 + 包缺失不建假行）；
  API summary 暴露 fleet_packages 全零初值——相关 4 套件 **44 passed**
- `check:quick` 16 gates OK（inner-imports 棘轮两次拦下我加的函数内 import，均已顶层化）；
  治理守卫 S1–S15 OK；ruff OK
- 迁移 head 唯一（c7d2e5f8a1b3），`pr-migrate-empty-db` 由 check:quick 覆盖

## Revisit

- 合入部署后：新 rev 上生产（sweep 是每日常设 timer，首轮后 summary/指标即有真值；
  可手动 `POST /script-presence/refresh` 加速首采）。
- 达标后把 alerts 规则 `tree|mixed > 0 持续 30m` 加进 site 告警面（单独小 PR）。
- 台账 `unisoc-env-path-keys`（10-31）：包面真实 scan 验证 + 本视图显示全 package 后，
  即具备删四路径键的机器条件。
