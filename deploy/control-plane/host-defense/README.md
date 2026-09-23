# 控制面宿主 OOM/卡死防线（#3200）

本目录是**事实源**。两条防线各管一段，缺一不可：

| 资产 | 落点 | 管的是 |
|---|---|---|
| `earlyoom.default` | `/etc/default/earlyoom` | 兜底：avail≤15% 且 swapfree≤8% 时杀掉用户态失控进程（SIGKILL 档 8%/4%） |
| `10-stp-watchdog.conf` | `/etc/systemd/system.conf.d/10-stp-watchdog.conf` | 兜底的兜底：PID1 30s 没喂狗 ⇒ 板载 iTCO 硬复位，不需要人到场 |

**第一道防线不在这里**，而是「测试执行必须带 cgroup 内存硬顶」——见
`docs/development/testing.md` §2。理由：earlyoom 触发时机器已经在失速边缘，而 2026-09-23
那次的失控体 120 秒就吃了 12.7 GiB；硬顶能把损失压到「一次测试运行」，兜底只能压到「丢一个进程」。

## 安装（控制面宿主，root）

```bash
R=/home/debian13/stability-test-platform          # 仓库根（deploy root）
cp -a /etc/default/earlyoom /root/earlyoom.$(date +%F)            # 先备份
install -m 0644 $R/deploy/control-plane/host-defense/earlyoom.default /etc/default/earlyoom
install -m 0644 $R/deploy/control-plane/host-defense/10-stp-watchdog.conf \
        /etc/systemd/system.conf.d/10-stp-watchdog.conf
systemctl restart earlyoom          # EnvironmentFile 只在启动时读
systemctl daemon-reexec             # manager.conf 由 PID1 重读，必须 reexec 而非 reload
```

`earlyoom` 包若缺失：`apt-get install -y earlyoom`（本目录不改包安装清单，见下「尾账」）。

## 验证（装完必须逐条绿，否则等于没装）

```bash
# 1) 防线参数生效且**不含 --dryrun**
tr '\0' ' ' < /proc/$(pgrep -x earlyoom | head -1)/cmdline | grep -o -- "--dryrun" \
  && echo "FAIL: 仍在 dry-run 空转" || echo "OK: 真防线"
# 2) 会选中元凶：--prefer 匹配的是 comm，失控体 comm=`python`
journalctl -u earlyoom -n 5 --no-pager      # 应打印 SIGTERM/SIGKILL 阈值两行
# 3) 硬件 watchdog 真武装（内核侧证据，不看配置文件）
systemctl show -p RuntimeWatchdogUSec --value          # 期望 30s
dmesg | grep -i "Watchdog running with a hardware timeout"
sudo fuser /dev/watchdog0                              # 期望 PID 1
```

第 3 条是硬要求：**「写了 drop-in」≠「狗被武装」**。PID1 只在 `daemon-reexec` 后重读
manager.conf，且 `RuntimeWatchdogSec` 读回 0 就是没武装（2026-09-23 事故前的状态）。

## 尾账（本目录刻意没做完的部分，别当成已完成）

- **未进 `tools/site_config/stages.py` 安装清单**：该文件当前由 #3098 在窗修改，避让中。
  进清单时还要注意两件：① 装完必须 `restart earlyoom` + `daemon-reexec`，只落文件会产生
  「已装但没生效」的假象（正是 #3050 批过的失效模式）；② `earlyoom` 不在
  `MONITORING_PACKAGES` 里，需要决定是纳入包清单还是降级为可选。
- **未进 `/etc/default/earlyoom` 的漂移检测**：`tools/dev/check-monitoring-assets.py` 只覆盖
  监控栈资产。在它纳入之前，本文件与运行副本的一致性靠上面三条验证命令人工保证。
