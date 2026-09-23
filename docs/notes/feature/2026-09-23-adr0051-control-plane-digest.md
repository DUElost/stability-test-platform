# ADR-0051 Phase 4 之一：控制面载荷摘要面 control-plane（2026-09-23）

Status: implemented
Class: feature

## Decision

给 `release-manifest.json` 加第三个 component `control-plane`，补 ADR-0051 §1.2 点名的
「控制面自身载荷无摘要面」缺口（#2269 构建机 `.env` 漏进 bundle 且「不在任何摘要面内」）：

- 输入集 = bundle 根下 `backend/**` **不深入** `backend/agent/`（agent 两面已各自有摘要），
  relpath 相对 bundle 根；符号链接跳过（与 agent 面一致）；
- 与 agent 面**相反，不排除** `.env*` / 字节码——构建 ignore 若被误改，这类文件一旦进 bundle
  必须改变摘要让 S0 拦下，而不是被豁免（豁免正是 #2269 的根因形状）；
- 实现放 `backend/agent/artifact_digest.py::collect_control_plane_entries`：build 端
  （bundle 内加载）与 install S0 量具（安装器自身树副本）共用同一文件、同一函数，
  无双侧镜像分叉面（与 agent 面「双实现 + parity」不同，这里天然单源）；
- S0 比对从「硬编码两键」改为「declared 的全部键都须匹配」：旧两键 bundle 照旧绿（兼容），
  新 bundle 声明了就必须对上；量具过旧不含新函数时 declared 有 control-plane → actual 缺
  → 红，方向 fail-closed；
- `REQUIRED_COMPONENTS` 不变（旧 bundle 不判缺）。

## Alternatives

- **把 dist-prod 也进 control-plane 面**：弃——nginx 直读、控制面进程不 import，属前端构建
  provenance 通道；塞进来只增加漂移面。
- **沿用排除 .env*（与 agent 面对称）**：弃——agent 面排除 `.env` 是因为部署流程自己写它；
  control-plane 面要守的恰是「构建机本地态混入」，不豁免才有牙齿。
- **新建独立量具模块**：弃——`_TRUSTED_DIGEST_SOURCE` 机制按单文件加载，新函数进同一模块
  即零改动地进入受信量具。

## Verification

- `tests/test_release_bundle.py` → 25 passed：三面独立重算等价（reference_digests 加第三面）、
  **分区隔离**（改 `backend/api` → control-plane 变、agent-code 不变；改 `backend/agent` →
  反之）、**`.env` 进摘要** 演示、旧两键 manifest 仍被 loader 接受；
- `tests/test_site_install.py` → 新增两例全绿：三面 bundle S0 `digest_matched`；
  manifest 生成后往 `backend/` 塞文件 → `release_digest` 红（load-bearing 反例证明）；
- `backend/tests/services/test_artifact_digest.py`（agent 面 parity 未受影响）+
  `tests/test_ansible_digest_contract.py` 合计 120 passed；`ruff` OK；
- CLI JSON 输出测试同步三面（暴露了 build 返回值遗漏并修复）。

## Revisit

- 本面落地后，「37 台旧载荷 + 发布根手改」类 drift 也能被 S0 与 digest 判据区分；
  生产下一次 bundle 上线（新 rev 构建）即自然启用三面，无运维动作。
- Phase 4 余下：展锐三族 / flashtool / aimonkey 入包与 `STP_UNISOC_*` 删键（前置 ADR-0042 v1.3
  豁免已提 #3216）。
