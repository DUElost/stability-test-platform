# bundle 布局的 `code_version` 空值修复（#3401 A4）

Status: implemented
Class: bug-fix

## Decision

修掉控制面自 bundle 布局运行时的溯源缺口：**构建期把短 sha 写进 bundle 的 agent 源树，读取端加文件回退**。

1. **写端**：`tools/release/build_bundle.py` 在计算 `short = revision[:8]` 后写
   `<bundle>/backend/agent/VERSION`（内容 = 短 sha + 换行）。bundle 是控制面现行部署形态
   （#2958 起 `stp-releases/current`），构建该 bundle 的 `build_bundle` 就是版本号的天然来源。
2. **读端**：`backend/services/host_updater.py::get_agent_code_version()` 保持「先 git」不变
   （仓库布局/开发/CI 走 `git rev-parse --short HEAD`），git 探测失败或返回空时**回退读
   `<agent 源树>/VERSION`**；内容需过 `_AGENT_CODE_VERSION_RE`（`[0-9A-Za-z][0-9A-Za-z._-]{3,39}`），
   不合形态即返回 `""`（坏文件不得写进主机 VERSION）。
3. **无身份影响**：`VERSION` 在 `_PAYLOAD_METADATA_EXCLUDES`（ADR-0040 D1）内——不进
   agent-code / control-plane 任何摘要面，也不随 tarball 下发（远端由 `write-version` 受控写入）。

**症状与证据链**（2026-09-26 实测，正是本项立项来源）：
- 远端更新脚本的版本写入是条件式的——`CODE_VERSION="{code_version}"; if [ -n "$CODE_VERSION" ]; then sudo "$PRIV" write-version …`；
- 控制面在 bundle 布局下 `git -C stp-releases/current/backend/agent rev-parse` 必然失败 → `code_version=""`
  → **write-version 被跳过** → 主机 `VERSION`/`agent_code_revision` 停在上一代；
- canary 经 API 热更新后 digest 已 `matched`、revision 仍 `1fafd052`（同日批量路径（repo checkout，git 可用）
  写的是 `0fa1d741`）——两条路径的分叉即本缺口的直接证据；收尾时用 wrapper
  `write-version` + `restart` 手工补齐了 canary（revision 是 agent **启动期**读取并缓存的，
  改文件必须伴随重启才对平台可见）。

## Alternatives

- **只在运维脚本/deploy 步骤写 VERSION**：弃——发布物之外的第二个事实源，且 CI 构建的 bundle 会缺它；
  `build_bundle` 已经持有 `revision`，写在这里单一且可测。
- **读 bundle 根的 `release-manifest.json`（`source.revision`）**：弃——把 `services` 层绑定到 bundle 布局的
  manifest 结构，而仓库布局（开发/CI）没有 manifest，得再造一个「布局探测」；
  `VERSION` 文件是两种布局都能有的最小公共面（仓库里它不存在时 git 分支照常生效）。
- **回退不校验形态**：弃——坏文件会被原样写进主机 VERSION（溯源文本造假），校验成本一行。
- **顺带改远端脚本，让空版本也写**：弃——空值本就不该写（会把主机 VERSION 清空），
  条件式写入是正确行为，缺口在**上游取不到版本**。

## Verification

- 新增测试：`backend/tests/services/test_host_updater.py` 三例（回退取值 / 无 git 无文件 /
  畸形文件忽略）+ `tests/test_release_bundle.py`
  `test_bundle_carries_agent_version_for_provenance`（bundle 内 `VERSION == revision[:8]`、
  端到端 `get_agent_code_version() == revision[:8]`、且 `VERSION` 不在 agent-code 枚举里）；
- 受影响面：`test_host_updater` + `test_release_bundle` + `test_site_install` +
  `test_ansible_digest_contract` + `test_artifact_digest` → **161 passed**；
- `python scripts/run_gates.py check:quick` → 见 PR 描述；
- **反向变异（临时，逐条复原）**：删掉文件回退 → 2 例红；删掉构建期写 VERSION → 1 例红；
- 生效边界：**当前在跑的 release（`0fa1d741`）建于本 PR 之前，不含修复**——所以本 PR 合入后需要
  下一次 `build_bundle` 产出的 release 才会自动带上 VERSION；届时可用 API 单台热更新复核
  「主机 `agent_code_revision` 随更新刷新」这一终态（本 PR 内无法端到端跑，已如实标注）。

## Revisit

- 若 release 目录布局变化（VERSION 落点改变），写端与读端必须同 PR 改（两处都有注释互相指认）；
- 若将来引入「控制面自身版本」的其他消费者（如 health 面展示），优先复用本回退而非再读 manifest；
- `_AGENT_CODE_VERSION_RE` 刻意宽松（sha / 版本串均可）；若要让「非 sha 形态」也成为合法溯源值，
  先回看本 PR 的取舍（当前只服务 `git --short` 与 bundle 短 sha 两种来源）。
