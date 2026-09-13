# ADR-0040 P1 切片①：artifact digest 双侧镜像与输入集契约（#1911 / #1900）

Status: implemented
Class: feature

## Decision

落地 ADR-0040（Accepted v1.0）§5-2 P1 最小闭环的第一片——**D1 身份模块**：

- **控制面** `backend/services/deployment_digest.py`：`compute_artifact_digest` /
  `compute_desired_digest`（D2 进程缓存，缓存键 = 输入集状态指纹
  `(relpath, mtime_ns, size, mode)`，含 `extra_files` 状态）/ `digest_input_files`
  （契约对账与诊断）。
- **Agent 侧镜像** `backend/agent/deployment_digest.py`：同算法纯 stdlib 实现，
  字节级等价（先例 script_catalog_version 双侧镜像），等价性测试守护。
- **身份格式**：`sha256:<hex>`；输入集 = 文件集规范化序列
  `(relpath_posix, 可执行位, 内容 sha256)` 按 relpath 排序、`\x00` 连接。
- **两类 artifact**（D1 分层）：`agent-code`（源码树 + `stp_schemas/pipeline_schema.json`
  arcname，**不含 resources/**）；`host-resources`（resources/ 除 `mtbf/`）。
- **extra_files 参数**：部署覆盖的树外文件（arcname → 真实路径）——schema 在
  载荷中的 arcname 参与身份，与「输入集 = 部署流程实际拥有并覆盖的文件集」一致。
- 排除集 = 现行 `_TAR_EXCLUDES`（文件/目录分列）+ ADR 增补（VERSION /
  ARTIFACT_DIGEST / .env / `.deps_installed_sha` / venv / logs）+ 打包同规则的
  `test_*.py` 源码测试排除；`resources/mtbf/` 永远属主机本地。

本片**无行为变更**（无消费方接线）——消费在切片 ②（显式列/心跳/远端写入）与
③（no-op gate / deployed_at 语义 / per-phase 计时），见 #1911 的实施顺序。

## Alternatives

- 只实现控制面单侧——ADR D1 明文要求双侧镜像 + 等价性测试（P2 Agent 自校验
  与故障诊断都需要第二实现）；先例（script_catalog_version）即双侧形态。
- digest 输入集自造排除清单——输入集契约必须与部署输入集一致（ADR §4.2
  「假阳性漏更新」缓解），故对账 `_build_tarball` 并由测试守护（见 Verification）。
- 缓存键用内容哈希——违背 D2「现算 + 进程缓存（缓存键 = 输入集状态）」；
  状态指纹（stat）命中时不做 229MB 级内容哈希。

## Verification

- `backend/tests/services/test_deployment_digest.py` 16 passed：双侧等价
  （两类 kind + extra_files 形态）、输入集边界（agent-code 三文件精确集 /
  host-resources 两文件精确集 / 身份 ≡ 剔除后的纯代码树 / resources 缺失 =
  确定性空摘要 / 未知 kind 拒绝）、敏感性（内容 / 可执行位 / 改名）、缓存
  （指纹未变跳过内容哈希 / 内容与 extra_files 变更失效）、**tarball 契约对账**
  （`_build_tarball` 载荷 − `resources/**` ≡ agent-code digest 输入集，schema
  arcname 参与两侧）。
- 过程修复：文件型排除项误列目录集、`mtbf` 剪枝未应用（两处均为镜像两侧同步
  修复，等价性测试同时覆盖）；缓存失效测试以 `os.utime` 显式推进 mtime（不依赖
  文件系统时间戳粒度）。
- `python scripts/run_gates.py check:quick` 7 gates 全绿。

## Revisit

- 切片 ②：host 显式列迁移 + 心跳上报 `agent_artifact_digest` + 远端
  `ARTIFACT_DIGEST` 受控写入（write-version 同族）。
- 切片 ③：no-op gate（digest 相等 → `converged(reason=digest-matched)`）+
  `agent_code_deployed_at` 语义修订（前端/测试同步）+ per-phase 计时。
- 切片 ④：四入口记录统一；`HOST_LOCAL_PATHS` 增补 `resources/`（§4.3 保护
  先行）须先于分层切换；P2 的独立通道与 ADR-0037 子命令回填另单。
- 本片无消费方，合并后至切片 ③ 前为「已测试待接线」状态——实施顺序与边界
  见 #1911。
