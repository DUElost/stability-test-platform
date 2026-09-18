# ADR-0047 起草记录：为什么是 Proposed 的容量单，而不是顺手调参数

Status: proposed
Class: architecture

## Decision

1. **产出形态 = Proposed ADR，不改一行代码/参数。** #703 的收口评论把这一面写成
   「方向级取舍，该走 ADR 而不是顺手调参数」；先例是 ADR-0046（同为会话起草、只列判据不代裁决）。
   顺手把 `pool_timeout` 设成 5s 是可行且五分钟的事，但它同时决定了两件未被裁决的事：
   高峰期请求的**失败形态**（快失败 vs 排队 30s 后 500）与**总量谁负责**（两份池相加无上限约束）。
   把参数当"止血"落地而不标注出口，正是本仓反复记账的那类腐化。
2. **把 D1（总量不变量）定为主判据。** 起草中最有确定性的一条事实是算术：单进程、两份池、
   每份 `30+60` → 峰值 180，而 `deploy/postgres/docker-compose.yml:30` 是 `max_connections=100`。
   两侧各自"合法"、合起来非法，且 env 允许运维单方面改一侧无人复核——这才是要裁决的东西，
   而不是"30/60 这两个数具体该是多少"。
3. **`n_instances` 现在就要进 D1 公式**，因为 ADR-0027（多实例）在同一个 M7 里推进；
   不留这个变量的话，多实例落地当天这份 ADR 就作废，且会以"新的"容量问题重开一次。
4. **D4（pgbouncer）显式绑定指标语义改写**：若引入外部池，`stability_db_pool_checkout_seconds` /
   `_failures_total` 的含义会失真（排队发生在代理侧而不是 SQLAlchemy 侧）。把"必须同 PR 改写 HELP/语义"
   写进裁决点，是为了避免最坏结局——**指标还在、说谎了**。这同 #2485/#2365（作用域请求改写全局 gauge）、
   #2286（恒真豁免）是同一族缺陷的不同入口。
5. **取向标注为"带出口的过渡"**：ADR §3 明写"丁（只补观测）作为下一步动作是对的，但必须写明出口"。
   这不是修辞——#2571 刚埋的三条序列目前**零消费方**，如果没有 D5 的阈值裁决，它们会长期停在
   "有数据、无判读"的状态。

## Alternatives

- **直接提参数 PR（缩池 + 设 pool_timeout）**：否决，理由见 Decision 1；且 §4 的三项证据缺口未补，
  任何具体数字都是猜的。
- **等 #703 ①（500 job / 30 host 压测回归）出结果再写 ADR**：否决。压测的判据本身要引用这份 ADR 的
  框架（"落在哪个桶 + 借连接 p99"才是可判读的成功标准），顺序上应当先有判据框架；
  且该 Execution 已在窗（其 PR 正文已引用 #2571 的池指标），两份工作互补而非重复。
- **把它写成 #703 的评论而不立 ADR**：否决。方向级决策留在 issue 评论里等于没有决策载体，
  下次同类讨论还得从头读 8 条评论。
- **合并进 ADR-0026（准入与规模化）**：否决。0026 管的是"任务能不能被接纳"，
  这一单管的是"控制面自己拿不拿得到连接"；合并会让 0026 变成两个不相干主题的容器。

## Verification

- **算术与配置逐条实测**（不是引用 issue）：`backend/core/database.py:200-231` 两函数各调一次
  `_pool_capacity_kwargs()`；`deploy/control-plane/systemd/stability-backend.service` 的 uvicorn
  命令行**无 `--workers`**；SAQ worker 由 `backend/main.py` lifespan 内
  `asyncio.create_task(_worker.start())` 起（**同进程**，不是独立进程，因此不按 3 份池算）；
  `deploy/postgres/docker-compose.yml:30` = `max_connections=100`。
- **一处起草期自纠（已回写正文）**：初稿在表格里写「全仓无 `pool_timeout`（grep 仅命中本 ADR）」。
  实际复核：`pool_timeout` 在 `backend/core/metrics.py` 的注释与 `backend/tests/test_database_config.py`
  的探针用例里各出现多次。准确说法应是「**生产引擎构造未设**」。
  ——这条区分很关键：前者是"这词不存在"（错，且容易被任何一次 grep 反驳掉），
  后者才是可行动事实（默认值 30s 正在生效，无人显式选择过它）。
- **链接与索引一致性**：新 ADR 的相对链接逐条 resolve 通过；`docs/adr/README.md` 主表与
  `docs/DOC-MAP.md` 各加一行，状态词与规范位版本（**Proposed** / v1.0）与头部一致——由 S12 校验，
  结果见 PR 门禁表。
- 本单**无代码/测试改动**，故不新增用例；门禁跑 `check:gov` 与 `check:quick`（见 PR）。

## Revisit

- **触发条件**：① §4 三项证据任一项补齐（尤其是 #2571 三条序列的分布基线，约需一周含一次规模 abort）；
  ② ADR-0027 推进到多实例（D6 的 `n_instances` 变实数）；③ 生产再次出现 `checkout_failures{kind="timeout"}`
  非零增量（那意味着 D2 的"快失败"分支被现实提前逼出）。任一发生即回到本单要 D1–D6 的裁决。
- **owner 裁决后本 ADR 的去向**：Accepted 则拆实现单（参数 + 启动期不变量守卫 + env 文档 +
  可能的指标语义改写），且 `AGENTS.md` 硬不变量是否收录 D1 需同时决定——若收录，
  S11 的锚点必须同 PR 更新（这是 #2546 评审里 F5 记过的同一条纪律）。
- 若长期无人裁决：本 ADR 保持 Proposed 并**不降级为"已知问题"**；D1 那条不变量最迟应在下一次
  容量类事故复盘时被拿出来，届时若仍未裁决，说明缺的不是判据而是裁决机制。
