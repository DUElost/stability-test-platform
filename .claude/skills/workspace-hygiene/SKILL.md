---
name: workspace-hygiene
description: 本地工作区与远程仓库的卫生治理 SOP——已合并 worktree/分支回收、孤儿 worktree 识别、远端残留分支清理、Execution registry 收口、/tmp 会话产物与僵尸容器盘点。触发时机：用户问「工作区/仓库有哪些需要处理」「卫生治理/清理/回收 worktree」「磁盘占用异常」「worktree 或分支堆积」，或在一轮批量交付（多 PR 合入、多会话并行）后做收尾盘点时。清理前先只读盘点、按「无价值/有价值」归类待用户裁决，不擅自删不可逆内容。
---

# 工作区与远程仓库卫生治理

一轮多 PR 交付或多个并行会话收工后，本地会积下三类东西：**已合入 main 的实体残留**（worktree、分支、远端分支）、**本机会话产物**（`/tmp` 文件、僵尸容器、明文凭据）、**协作状态残留**（Execution registry 里的僵尸记录）。

本 skill 的执行原则：**先只读盘点并归类，再由用户裁决删除**。可逆的（回收 worktree、收口 registry）授权后即做；不可逆的（删文件、删卷）先归类再动。

## 0. 前置：fetch 先行

盘点必须基于**新鲜的** `origin/main`，否则会把已合入的分支误判成未合、或对已删的远端分支做无效操作：

```bash
git fetch --prune
git status -sb | head -1        # main 是否落后
```

工作树脏时**先别动任何回收**——脏内容可能是并行会话的在飞产物。

## 1. 只读盘点

仓库自带一个只读盘点脚本（不做任何修改），一次跑完所有面：

```bash
bash .claude/skills/workspace-hygiene/scripts/hygiene-scan.sh
```

输出分四段：分支、worktree、远端、`/tmp` 与容器。手工盘点时按下述判据读。

## 2. 判定判据

### 2.1 分支是否已合入 —— 不要用「有没有 PR」判

`gh pr list` 会对已被 squash/rebase 或经其他 head 名合入的分支返回空。用祖先关系判：

```bash
git rev-list --count origin/main..<branch>          # 0 = 无独有提交
git merge-base --is-ancestor <branch> origin/main && echo YES
```

`unique=0` **且** 祖先判定为 YES 才算已合入，可回收。

`unique>0` 的分支不要直接删——先逐个看那几条游离提交的内容是否已被 main 以其他路径覆盖（`git show <sha> --stat` + 在 main 的对应文件里 grep 关键行）。确认零增量后删除，确有增量则 cherry-pick 抢救。

### 2.2 worktree 回收 —— 双复验

```bash
git -C <worktree> status --porcelain | wc -l   # 必须 0
git rev-list --count origin/main..<branch>    # 必须 0
```

两个条件任一不满足就跳过该 worktree。批量执行时把复验写进循环，宁可少收也不要误删在飞现场。

### 2.3 孤儿 worktree —— 逃过 `git worktree remove` 的那类

**这是最容易漏的一类。** 某些 worktree 目录的 `.git` 指针指向的 gitdir 元数据已被 git 清理（外部清理、误操作、或工作树被整体删过），于是它：

- **不出现在 `git worktree list` 里**（git 已经没有它的记录）
- **`git worktree remove` 也管不到**（不是已注册 worktree）
- 只在磁盘上留一个目录，`rm -rf` 时若含 root 属主文件会中途失败留下空壳

只能靠**扫目录**发现。判据：

```bash
cat <dir>/.git                    # gitdir: /path/.git/worktrees/<name>
t=$(sed 's/gitdir: //' <dir>/.git); [ -e "$t" ] || echo "孤儿：元数据已失联"
```

本仓库的孤儿通常出现在 `.wt/`（已被 `.gitignore` 忽略，所以不会进 git status）和 `/tmp` 下。`git worktree prune --dry-run -v` 查不到它们。

### 2.4 远端分支

先看它是否还有未合入内容（§2.1），再看对应 PR 的真实状态（`gh pr list --head <b> --state all`）。确认已合入即可 `git push origin --delete <b>`，再用 `git fetch --prune` 清跟踪引用。

## 3. Execution registry 收口（两步，顺序不能反）

registry 里的 Execution 记录会滞后于真实交付，这是**已知性质**，不是异常。

```bash
python3 tools/dev/ai_work.py status            # 派生视图：lifecycle × integration × liveness
```

第一步，刷新已登记 PR 的终态：

```bash
python3 tools/dev/ai_work.py update --all
```

**它只改 `integration`，不改 `lifecycle`**——没登记过 PR 的记录无从自动归终态。所以第二步要自己筛出「工作已合入却仍标 CODING」的矛盾态，逐条收口：

```bash
python3 tools/dev/ai_work.py status \
  | grep 'lifecycle=CODING' | grep 'integration=MERGED' | sed 's/: .*//' > /tmp/.zombies.txt
while read -r id; do python3 tools/dev/ai_work.py finish --id "$id"; done < /tmp/.zombies.txt
```

收口判据与红线：

- **只 finish `integration=MERGED` 的**。`NO_PR` 的记录无法机械验证工作是否进了 main，需逐条核对其 scope，不能批量处理。
- **绝不手工删除记录**。registry 是 §3.4 并行撞单查重的依据面，删记录会破坏在窗判定；`finish` 只把生命周期推到终态，误收了可以用 `resume` 退回 CODING 返工。
- `last_seen` 是本机 liveness，会滞后**且不能单独作为「有人在用」的判据**。真实判据是 worktree 的实际 dirty 状态与内容是否已合入（§2.2）。反过来，**近期 last_seen 不代表不能收口**——核实 PR 确已合入后照收。

## 4. /tmp 会话产物

盘点按日期分布比逐条看可靠：

```bash
cd /tmp && ls -lAt --time-style=+'%Y-%m-%d' | awk 'NR>1 {print $6}' | sort | uniq -c
du -sh /tmp/* /tmp/.[!.]* 2>/dev/null | sort -rh | head -20
```

（`find -newermt` 在部分环境下返回异常，优先用 `ls -lAt`。）

### 4.1 绝不能删的

- **正在运行的 AppImage 挂载点**（`.mount_*`）：ZCode、clash 等以 fuse 挂载在 `/tmp`，删掉会崩掉正在跑的会话。判据看 `mount | grep -i ZCode` 和进程年龄，**不看文件日期**：
  ```bash
  mount | grep -iE 'ZCode|clash'
  ps -eo pid,etimes,args --no-headers | grep -iE 'ZCode|clash' | grep -v grep
  ```
- **已注册的 git worktree**（如部署构建用的 `/tmp/stp-deploy`）——它出现在 `git worktree list` 里就说明还受管理。
- **活 IPC**：`.sock`、`claude-*`、`codex-bwrap-*`、编辑器 profile 目录。
- **当日及其他并行会话在写的文件**。

### 4.2 凭据

`/tmp` 下的 token/密钥文件按仓库纪律**只删不读**——确认存在性后直接删除，不打开内容：

```bash
ls -la /tmp/*tok* /tmp/*token* 2>/dev/null | awk '{print $5, $9}'   # 只看大小和路径
rm -v /tmp/<file>
```

### 4.3 可删的典型

过期安装包（先确认没有服务在用它：跑的是 `/usr/bin/prometheus` 就与 `/tmp/prometheus-*` 无关）、解包的构建目录、pytest `tmp_path` 产物、Next.js SSR dump、profiler 虚拟环境、CI 日志副本（GitHub 上已有）、多天前的会话产物（PR body 草稿、审计 JSON、评论稿）。

删除会撞上系统保护目录（`systemd-private-*`、`.X11-unix`、`.ICE-unix`、`.font-unix`）——这些报「不允许的操作」是正确行为，不要用 sudo 强删。

## 5. 容器与僵尸栈

容器长期 unhealthy 时，**先别改 healthcheck**。真实原因常常是 bind-mount 的宿主源目录已被删除：

```bash
docker inspect <c> --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
ls -ld <上面输出的路径>          # 不存在 = 源没了
docker exec <c> true             # 报 "current working directory is outside of container mount namespace root" = 已废
```

源目录消失后容器只是靠已映射页苟活（`docker logs` 仍有输出、`ps` 仍在），但 `docker exec` 完全不可执行、healthcheck 永远不可能通过——这是**僵尸栈**，应整体下线而非修 healthcheck。

下线时注意：compose 项目的 working_dir 已不存在则 `docker compose down` 跑不起来，改用 docker 直接操作，并按项目标签确认范围：

```bash
docker inspect <c> --format '{{index .Config.Labels "com.docker.compose.project"}}'
docker volume ls --filter label=com.docker.compose.project=<project>
docker rm -f -v <该项目的容器...>
docker volume rm <该项目的命名卷...>
```

**本机可能同时是生产控制面宿主**：生产是 systemd 服务，不在 docker 里；确认容器属于哪个 compose 项目再删，删前核对没有进程占用、没有别的项目挂载同一卷。

## 6. 仓库文件改动走 PR

`.gitignore`、`docs/` 这类仓库文件的修改**不能直推 main**（AGENTS.md 硬规则）。建分支 → 改 → 跑 `check:quick` → 推 → 建 PR，并按 Execution 契约 declare：

```bash
git checkout -b <type>/<slug>
# ... 修改 ...
.venv/bin/python scripts/run_gates.py check:quick
python3 tools/dev/ai_work.py declare --requirement "..." --harness <h> \
  --worktree "$(pwd)" --scope "<改动的路径>" --test-impact none
git add <显式列文件>          # 禁 git add -A：会把 harness 本机态扫进树
git commit && git push -u origin <branch>
```

未跟踪的 harness 本机态文件（如 `.zcodeignore`、`.cursor_*`）应加进 `.gitignore` 防误提交。

## 7. 收尾

- 复核终态：worktree 列表、分支列表、容器数、`/tmp` 规模、磁盘。
- 把本轮**性质判断**（哪些已合入、哪些被覆盖、哪些需人工核验）写进滚动台账并标注记录日与现查命令；issue 的开关状态以 GitHub 现查为准，不抄进台账。
- 授权清理的 sudo 项目逐条回报，失败的（root 属主残留）如实说明并保留为显式尾巴。

## 8. 易错点

| 现象 | 根因 | 处置 |
|---|---|---|
| 明明已合入却报「未合」 | 用了 `gh pr list` 判断，或基于陈旧的 `origin/main` | 先 `fetch --prune`，用 `merge-base --is-ancestor` 判 |
| `git worktree remove` 找不到某个目录 | 孤儿 worktree：gitdir 元数据已失联 | 扫 `.wt/` 与 `/tmp`，按 §2.3 判据确认后 `rm -rf` |
| `rm -rf` 中途失败留下空壳 | 目录内有 root 属主文件（如 `.docker/`） | 主体先删，剩余空壳如实上报，等 sudo 授权 |
| registry 收口后 CODING 计数没降 | 只跑了 `update --all`，它不改 lifecycle | 补第二步：筛 `CODING+MERGED` 逐条 `finish` |
| `git check-ignore` 给出意外结果 | shell cwd 卡在已删除目录，路径解析到了别处 | 显式 `cd` 到仓库根再执行 |
| registry 记录「刚刚还在动」 | `last_seen` 是本机 liveness，会滞后 | 以实际 dirty 状态与合入情况为准 |
| 删了 `/tmp` 大目录后应用崩 | AppImage fuse 挂载点正在使用 | 先 `mount` + 进程年龄判定，见 §4.1 |
| 容器 unhealthy 修不好 | 宿主 bind-mount 源目录已被删，是僵尸栈 | 查 working_dir 路径是否存在，整体下线 |
