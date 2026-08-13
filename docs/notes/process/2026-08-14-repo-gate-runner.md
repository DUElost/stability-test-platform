# 门禁单入口 scripts/run_gates.py
Status: implemented
Class: process

## Decision

`scripts/run_gates.py` 是本地与 CI 共用的门禁矩阵单一入口：

- `check:quick`：ruff / eslint / tsc / knip / compileall（纯静态，最快一轮）；
- `check:pr`：quick + pollution / immutability / agent-tests（与 PR CI 逐项一致）；
- `check:full`：全部（含 PG 套件、vitest、build、docker，归夜间全量）。

失败即停；用 `python -m` 形式调用（规避「裸 pytest 落到另一套解释器」
的历史坑）；immutability 的 base 用 `STP_GATE_BASE_REF` 覆盖（CI 侧传 PR base）。

## Alternatives

- 保持 ci.yml 内联命令 + `scripts/run_pytest.sh` 等散装脚本：拒绝。每个新
  会话都要重新推导「先跑什么、哪个与 CI 一致」，本地与 CI 漂移。
- ci.yml 本轮不动：后续逐 job 把 `run:` 行替换为对应 profile，管线形状不变。

## Verification

`check:quick` 全绿 = 与 CI 的 lint/typecheck 等价；`check:pr` 与 PR 必查项
逐项对应（对照 ci.yml 逐行核对过）。

## Revisit

ci.yml 全部 job 替换为 profile 调用后，本 note 更新为「CI 已全量接入」。
