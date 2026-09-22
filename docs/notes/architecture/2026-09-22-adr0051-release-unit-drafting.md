# ADR-0051 起草——发布单元与内容寻址（2026-09-22）

Status: proposed
Class: architecture

## Decision

把 2026-09-22 用户两轮逐项核对后定稿的治理方案落成 **ADR-0051（Proposed v0.1）**，
不实现任何代码。核心一句：**不可变性从源码目录移到内容寻址包**——脚本膨胀、部署源与
开发工作区同一棵检出、外部工具无包形态，三个被分别治理（ADR-0039 / ADR-0046 /
ADR-0033 Phase B）的症状是同一个病根。

起草时做了三件事，决定了 ADR 的形态：

1. **正面回应 ADR-0039 D6**（§2）。「每族一棵源码树」按 ADR-0039 词表就是被否决的 P2。
   否决理由四条全部以「运行时事实面 = 源码目录」为前提；本 ADR 撤销该前提而非撤销 D6。
   每版独立可校验由 per-version tarball 的 `package_sha256` 承担；`_` 前缀盲区在整包 sha 下
   不存在（`tool_cache.ensure_package` 已如此实现）。
2. **不整篇 supersede ADR-0039**，显式继承 D2/D3/D4/D5/D7 并把作用域从目录改为包（D5）。
   删包与删目录一样不可逆，且包仓库是多站点共享面，D2 的理由更强。
3. **落地写成「接通已有链路」**：`build_bundle.py` / `site_config install` /
   `release_manifest.py` 已在，真实缺口只三块（控制面摘要面、tool 包条目、本机 checkout
   形态）；第 1 步是切 unit 与 `STP_SCRIPT_ROOT`，不是建 release 目录概念。

## Alternatives

- **整篇 supersede ADR-0039 / ADR-0046，「一次裁决拍一次」**：弃。实测 S12 ⑤ 要求锚目标
  ADR 头部必须 Accepted（ADR-0039 入向 40 文件、ADR-0046 12 文件），S11 锚注释自陈
  「ADR-0039 Accepted 当日须同 PR 改写本锚」，共享元文件须串行领单——改成「一次裁决 +
  §9 四组同 PR 机械改动」。
- **迁移期冻结新增版本目录（钉 208）**：弃。实测 09-13→09-22 新增 47 个目录，与前版
  差异 ≤5 行的为 0——冻结挡的是真实修复；且 `default_params` 硬不变量逼迫参数改动也开
  新版本。改为 `MIGRATION-EXCEPTION` 声明 + 按周上调的棘轮（与 `check_new_script_family`
  同载体判据）。
- **「script 表新增包 sha 列」写成沿用 ADR-0033 D3**：弃。D3 原文 `content_sha256 :=
  tarball sha256` 与 v1.12 实际裁决 C1（整包 sha 与入口 sha 分离）互斥，写成对 D3 一句
  措辞的修订，采 C1。
- **第 2 步一体推进**：弃。`tool_cache.resolve_packaged_scan_tool` 全程不查 script 表，
  35 族走 `ScriptRegistry.resolve → entry.nfs_path` 进 argv，「DB 权威→包身份」这一环
  现有代码里不存在，且 `pipeline_engine.py` 有三处 `nfs_path` 形态耦合（`parents[3]`
  PYTHONPATH / `cwd=dirname` / `"/flash_firmware/" in path`）。拆 2a（打包登记等价证明，
  `nfs_path` 不动）与 2b（切执行路径），Phase 3 删目录只依赖 2a。
- **等价证明基准取 git 历史首次发布 commit**：弃。生产审计表实测 `scan_rebaseline`
  执行过 5 次（07-31 至 08-31）、重锚 24 个版本（3 个仍被引用），这些行的 sha 记录的是
  「某次盘上状态」；但当前 `origin/main` 字节与 DB sha 对全部 208 行一致，故基准取当前字节。

## Verification

只读实测（2026-09-22，`origin/main` = `881478cf`；生产库经 `.env.backend` 连接串只读
`SELECT`，凭据未落盘）：

- 版本目录 30/100/111/175/208（08-01/09-01/09-10/09-15/09-22），行数 7.5k→128.7k；
  09-13 后新增 47 目录、≤5 行差异 0；
- `script` 210 行 / 活跃 99；`plan_step` distinct 引用 52；208 目录与
  `content_sha256` + `support_files_manifest` 逐字节一致，2 行无目录均 `is_active=false`
  （`unisoc_probe@1.0.2`、`gpu_setup@1.0.3`）；
- `audit_logs.action='scan_rebaseline'` 5 次，重锚 24 版本，仍被引用 3；
- ADR-0039 D6 / 可行性研究结论 2 原文；ADR-0033 D3 第 136 行 vs v1.12 C1；
  `tool_cache.py:126/142/143` 校验顺序；`pipeline_engine.py:804/1787/1816` 三处耦合；
  `.gitignore:20/85` 与 `host_updater.py:46` 物料回退；`check_governance_surface.py:50/286/322`
  S12 ⑤ / S11 锚 / 状态词表；unit 第 21/24 行有无减号对照。
- 门禁：`gov-surface` gate 见 PR 描述记录的实际运行结果。

未做：任何代码、S11/S12 锚改写、ADR-0039/0046 状态变更（均为 Accepted 当日 §9 事项）。

## Revisit

- owner 裁决 §10 五个点后转 v1.0；否决 D1 即整篇作废、本 Note 归档；
- Phase 2a 等价证明脚本落地时，若出现任一 `false`，回到本 Note 的「基准取当前字节」
  假设复核（可能是 2a 期间又发生了 `scan_rebaseline`）；
- `ADR-0042 D3` 修订（Phase 4 删 `STP_UNISOC_*` 的前置）单独立单。
