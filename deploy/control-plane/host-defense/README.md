# 控制面宿主 OOM/卡死防线（#3200）

本目录是**事实源**。两条防线各管一段，缺一不可：

| 资产 | 落点 | 管的是 |
|---|---|---|
| `earlyoom.default` | `/etc/default/earlyoom` | 兜底：avail≤15% 且 swapfree≤8% 时杀掉用户态失控进程（SIGKILL 档 8%/4%） |
| `10-stp-watchdog.conf` | `/etc/systemd/system.conf.d/10-stp-watchdog.conf` | 兜底的兜底：PID1 30s 没喂狗 ⇒ 板载 iTCO 硬复位，不需要人到场 |

**第一道防线不在这里**，而是「测试执行必须带 cgroup 内存硬顶」——见
`docs/development/testing.md` §2。理由：earlyoom 触发时机器已经在失速边缘，而 2026-09-23
那次的失控体 120 秒就吃了 12.7 GiB；硬顶能把损失压到「一次测试运行」，兜底只能压到「丢一个进程」。

## 安装（两条路，优先第一条）

**① 站点安装器（推荐）**：资产已在 `tools/site_config/stages.py` 的 `host_defense_artifacts()`
清单里（`HOST_DEFENSE_DISTRO_DEFAULTS` + `HOST_DEFENSE_ASSETS`，与监控栈合成 `host_assets()`）。
正常 `site install` 会：S1 装 `earlyoom` 包 → S2 渲染两份资产（渲染期即拒绝 `--dryrun` 的
生效行）→ S4 落盘 → `systemctl restart earlyoom` → `systemctl daemon-reexec` → **回读验证**
（`is-active` + `/etc/default/earlyoom` 的生效行 + `RuntimeWatchdogUSec` 非 0）。
任一项不过 = `install_host_defense` 红灯，安装器不会带着没武装的狗报绿。

> 防线**不挂在 `monitoring.enabled` 上**（与采样器不同）：关掉观测面可以接受，关掉
> 「卡死后自动复位」不能接受——测试
> `test_host_defense_is_not_gated_by_the_monitoring_switch` 钉住这一点。

**② 存量控制面宿主（installer 之前的机器）手工重放**：

```bash
R=/home/debian13/stability-test-platform          # 仓库根（deploy root）
cp -a /etc/default/earlyoom /root/earlyoom.$(date +%F)
sed "s#<deploy-root>#$R#g" $R/deploy/control-plane/host-defense/earlyoom.default \
  | sudo tee /etc/default/earlyoom >/dev/null
sudo mkdir -p /etc/systemd/system.conf.d
sed "s#<deploy-root>#$R#g" $R/deploy/control-plane/host-defense/10-stp-watchdog.conf \
  | sudo tee /etc/systemd/system.conf.d/10-stp-watchdog.conf >/dev/null
sudo systemctl restart earlyoom && sudo systemctl daemon-reexec   # 缺一不可
```

## 验证（装完必须逐条绿，否则等于没装）

```bash
# 1) 防线参数生效且**不再是 dry-run**（只看 EARLYOOM_ARGS 生效行）
grep '^EARLYOOM_ARGS' /etc/default/earlyoom | grep -c -- --dryrun        # 期望 0
# 2) 硬件 watchdog 真武装（内核侧证据，不看配置文件）
systemctl show -p RuntimeWatchdogUSec --value                            # 期望 30s
sudo dmesg | grep -i "Watchdog running with a hardware timeout"
sudo fuser /dev/watchdog0                                                # 期望 PID 1
# 3) 运行副本与仓库事实源一致（漂移检测已把这两项纳入清单）
venv/bin/python tools/dev/check-monitoring-assets.py --repo-root . | tail -3
```

第 2 条是硬要求：**「写了 drop-in」≠「狗被武装」**。manager.conf 只在 `daemon-reexec`
时被 PID1 重读（`daemon-reload` 不行），回读到 0 就是没武装——那正是 09-14 到 09-23 之间
这台机器的真实状态。

## 尾账

- ~~未进安装清单/漂移检测~~ → **已做**（本目录资产进 `host_assets()`，`check-monitoring-assets.py`
  覆盖，2026-09-23 本机实测 `match`）。
- 仍开着：**#3202**（Phase-3 WIP 里那条 `pytest` 失控环，≈160 MB/s）与 **#3050 G1**
  （宿主内存告警按现网分布重标为结果判据 + 短 `for:`）。硬顶（第一道防线）见
  `docs/development/testing.md` §2。
