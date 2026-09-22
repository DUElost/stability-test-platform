# 部署 SOP 两处 `⚠️待校对` 项的实机验证：刷机前置库 + 带外资源身份

Status: implemented
Class: process

## Decision

`control-plane-deploy` skill 里最后两处 `⚠️待校对`（§6 的 SP Flash Tool 缺库行、§3 的带外资源
身份行）此前只有代码侧依据、没有真机数据。本次用**只读探针**（ansible `script` 模块，走仓库
既有 inventory，无写操作）在 48 台生产主机上取数，两项都已收口：

### ① §6「SP Flash Tool host 缺库」——包名对、场景当前存在、处置入口写错了

- **包名与工具真实依赖一致**：控制面与真机各跑一次 `ldd`。工具自带 Qt4（`lib/` 目录），
  系统侧真正缺的是 `libICE.so.6 / libSM.so.6 / libXrender.so.1 / libfontconfig.so.1 /
  libglib-2.0.so.0`——正是那五个包；缺库主机上 `ldd` 有 **11 个未解析依赖**，包含这五个。
  （在本机直接 `ldd` 会因 Qt4 未安装而只报 `libQtGui.so.4` 之类，必须先
  `LD_LIBRARY_PATH=<工具目录>:<工具目录>/lib`，否则判据会被误导。）
- **场景当前存在，不是历史**：48 台中 **38 台五库齐、10 台五库全缺**，缺库的 10 台**全部在
  `agent_legacy` 组**（该组 34 台）。缺库主机上刷机前置检查（`flash_preflight` 的 qt-libs 步）
  会明确失败并给出指引——是 fail-closed，不是静默。
- **处置入口原先写偏了**：旧文让人手敲 `apt-get install`；按 ADR-0037 D5「provisioning 归位、
  运行期脚本不装包」，正经入口是平台通道
  `POST /api/v1/hosts/{id}/flash-prereqs/ensure`（= `tools/ansible/playbooks/ensure_flash_prereqs.yml`，
  内含 dialout、MTK ttyACM udev 与**五库的 `t64` 兜底**）。手敲 apt 降级为平台不可用时的逃生阀，
  并补了 Debian 13 的坑：`libglib2.0-0` 是过渡名，`apt` 装别名返回 rc=0，但**复查要查
  `libglib2.0-0t64`**，否则会得到「装了又说没装」的假判。

### ② §3 带外资源——「不抹」成立；但「已收敛」这个读法不成立

- **protect-only 实测成立**：当日两轮 code 推送（48 台）之后，主机 `resources/`（aimonkey 82M +
  flashtool 149M）仍在位、目录与文件 mtime 未变 ⇒ `resources/***` 的保护确实是有效的
  （与 #1950/#2019 的代码与契约测试一致），08-31 那条「热更新清带外资源」的旧形态确已被修。
- **但身份是自报的**：主机上报的 `agent_resources_digest` 就是部署流程写进 `ARTIFACT_DIGEST_RESOURCES`
  的内容（`stp_agent_priv.write-digest` 只校验 `sha256:<hex>` 格式，从不重算），于是
  `plan_convergence` 的 `resources_drift` 实际在比对「控制面写的值 vs 控制面期望」——主机真实
  内容不参与判定。
- **逐台盘点（48 台全量，用主机上同一份 `artifact_digest.py` 现算；先核对两侧模块 sha256 相同，
  排除实现偏）**：code 侧 **48/48** 与期望一致；resources 侧 **41/48 一致、7 台偏离**。7 台的
  偏离值相同，逐文件比对后确认**差异只是 CRLF→LF**（把控制面副本归一化后 sha 与主机逐一相同）
  ⇒ 语义同一、字节不同。
- 时间线佐证来源：一致的 41 台保留了源文件 mtime 与 08-12 的目录 mtime（rsync 保 mtime 的字节级
  拷贝）；偏离的 7 台文件 mtime 被统一改写成 2026-09-15 20:01–21:06（重写而非拷贝），同期主机上
  留有 `agent.bak.<时间戳>` 形式的安装备份目录 ⇒ 那次舰队维护走了一条会归一化行尾的通道，而平台写下的身份
  是「意图值」。
- **收口**：SOP 两行改为实测结论（含判据与处置入口），并把机制缺口立为 issue #3128（身份必须来自
  主机实测：心跳/巡检本地重算、`write-digest --verify`、或把 `verify_scripts` 扩到 resources 层）。

## Alternatives

- **只在 SOP 里保留「未验证」标注、不动**：否决。两项都已取得真机证据，继续标「待校对」等于把
  已建立的判据继续当假设用，下一个人还会重跑一遍。
- **用 `flash_preflight` 跑一轮 PlanRun 来验刷机前置**：否决。那要建 Plan/PlanRun 并占用设备与
  时间窗，而它检查的正是 dpkg 五包与 ldd 能直接回答的东西——用重锤验一个 `ldd` 结论。
- **只看 host 上报的 `agent_resources_digest`（徽标 matched）就宣布 resources 没问题**：否决。
  这正是本次被证伪的读法（7 台实测偏离却全判 converged）。
- **把 7 台的字节差异当「内容不同」写进结论**：否决。逐文件哈希对拍（归一化后逐一相同）证明只是
  行尾，报告若写「内容不同」就是夸大了影响；真正的问题是**平台测不出来**，不是这 7 台坏了。
- **顺手在 7 台上做一次 `--force` 收敛把行尾抹平**：本轮否决。那是生产写操作，且会把「一个可测
  的偏离样本」清掉——#3128 的验收需要它作为反例存活到修复落地。
- **把探针脚本落成 `tools/` 下的工具**：本轮否决（见 Revisit）：验证需求已满足，而正确的长期出路
  是让主机自测（#3128），落一个会被平台修复取代的旁路工具会给后人留两条口径。

## Verification

- 探针全部只读（`ls/stat/sha256sum/dpkg-query/find/ldd/python3 -c` 计算 digest），**未写任何文件**；
  经仓库既有 `tools/ansible/inventory.ini` 与 ansible `script` 模块执行，48/48 台全部可达、0 失败。
- 关键数值：① 缺库 10 台 = `agent_legacy` 全体缺库数，其余 38 台齐；缺库主机 `ldd` 未解析依赖 11 个。
  ② 身份对拍 48 台：code 48/48 一致；resources 41 一致 / 7 偏离；7 台的 calc 值同为
  `sha256:1b1544d7…`、stored 同为 `sha256:fa6f8bc9…`；偏离的 10 个文件归一化行尾后 sha 逐一相符。
- 反例存活：偏离样本（7 台）本轮**未**做收敛，留给 #3128 的验收标准（「改/删一个 resources 文件
  → 平台应在约定周期内判出 drift」）。
- 未验证（如实）：为什么只有这 7 台走了会归一化行尾的通道，根因未定位（只在 mtime 与备份目录上留下
  时间线证据）；10 台缺库主机**是否需要刷机能力**属业务判断，本轮只给出事实与处置入口，未代为执行
  provisioning。
- `python scripts/run_gates.py check:quick` 与 `ip-leak`（本次文档不含任何主机地址，只写组名与计数）。

## Revisit

- **#3128 落地后**：把「身份自测」写进本 SOP 的判据（届时 §3 的探针段落改为引用平台能力），并复核
  那 7 台是否被自动收回一致。
- **10 台缺库主机的归属**：若确认它们需要刷机，处置是 `POST /hosts/{id}/flash-prereqs/ensure`
  （一次 ansible 归位）；若不需要，应在 SOP 或资产台账里写明「非刷机机位」，免得每次巡检都重新质疑。
- **探针可否落成工具**：当 #3128 给出平台侧自测后，本 Note 里的探针只保留「诊断用」角色；
  若届时仍需要人工对拍，再考虑落 `tools/`（含测试），不要在此之前留两条口径。
- **行尾不变量**：`resources/**` 的 CRLF 由控制面树决定，而仓库对它有 `.gitattributes` 之类的约束吗？
  本轮未查。若之后再有行尾类偏离，值得先看这一层，而不是逐台救火。
