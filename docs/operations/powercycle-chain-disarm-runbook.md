# 开关机专项的中止与"链路解除"运维手册

> **警告（先读这条）**：`powercycle` 作业的 `COMPLETED` / `ABORTED` **不代表设备侧链路已解除**。
> 解除的唯一判据是设备侧回读到 `running=false`。账面完成而链路仍武装时，设备会在无监督下继续
> 自主重启（最长到 `test_times` 上限 1000 轮），并在随时把跑在它上面的其它专项打断。
> 背景与实证：[ADR-0061](../adr/ADR-0061-device-return-contract.md)、
> [#3714](https://github.com/DUElost/stability-test-platform/issues/3714)、
> [#3696](https://github.com/DUElost/stability-test-platform/issues/3696)。

> **过渡措施**（ADR-0061 D8 第 4 条）：本手册是 ADR-0061 D5「欠账设备不交付，并自动清偿」生效前的人工替代，
> 出口为 D5 生效。D5 生效后，作者必须把本手册退役，或改为只保留排障内容。
> 本手册只覆盖开关机专项。休眠、MTBF、GPU、Monkey 专项中止后同样可能留下设备侧执行体（ADR-0061 §1.1），
> 目前没有对应手册；载体见 [#3718](https://github.com/DUElost/stability-test-platform/issues/3718)。

适用：`specialty_key=power-cycle` 的任何计划（如 `MLD_NX2-开关机专项-18H`、`周期回归-开关机-1h`）。
主机 IP 一律写作 `172.21.x.x` + 末段；序列号按掩码 `AHFFNX69****0169` 形式记录。

## 1. 中止后必做的解除核验（每台中止过的设备）

1. 取该 host 上实际接入的设备（**以 host 的 sysfs 枚举为准，不信平台 `host_id`**——现场挪动过设备）：

   ```bash
   ssh android@172.21.x.x 'for d in /sys/bus/usb/devices/*/; do
     [ "$(cat $d/idVendor 2>/dev/null)" = 0e8d ] || continue
     echo "$(basename $d) pid=$(cat $d/idProduct) serial=$(cat $d/serial 2>/dev/null)"; done'
   ```

   预期：`pid=201c` 且 `serial` 可读 = 正常态；`2000/2001/0003/2026/3000` = 刷机态或异常态（另按刷机手册处置）。

2. 回读链路标志（**必须在 `adb root` 之后重连再读**，否则 adbd 重启瞬间读不到文件，会假报"无 prefs"）：

   ```bash
   A=/usr/bin/adb; S=<serial>
   $A -s $S root; sleep 4          # 等 adbd 以 root 重新起来
   $A -s $S shell "grep -o 'running\" value=\"[a-z]*' \
     /data/data/com.tinno.autotesttool/shared_prefs/powercycle_runner.xml"
   $A -s $S shell "cat /proc/uptime | cut -d. -f1"
   ```

   预期：`running" value="false`；且相隔 10s 以上两次 `uptime` **连续增长**（不归零）。
   若 `running=true` 或 uptime 反复归零 → 该设备仍在武装，进 §2。

3. 记录：`device_id / serial 掩码 / host 末段 / running / uptime / current_count`。这些字段是
   后续判断"要不要现场干预"的唯一依据，不要只记"已中止"。

## 2. 可达设备的解除（标准动作）

用现成清理计划（示例为 `.85` 的 `plan 79`，步骤只有 `powercycle_finish`）：

```bash
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Origin: http://127.0.0.1' \
  -H 'Content-Type: application/json' \
  -d '{"device_ids":[<单个设备id>],"note":"解除武装链路"}' \
  http://127.0.0.1:8000/api/v1/plans/79/run
```

> **警告（必须一台一个 run）**：准入是**整 run 原子**的
> （`admission_pump.py`：*"one PlanRun admits as a unit … no partial per-host admission"*）。
> 把"在线设备 + 离线设备"或"两台离线设备"塞进同一个 run，会导致任一设备不可达时**整个 run 反复重排队**，
> 结果谁也清理不了。混合派发的直觉在这里是错的。

预期：`cleanup COMPLETED`。之后回 §1 第 2 步复测 `running=false`——**以复测为准**，不以作业状态为准。

## 3. 不可达（关机/离线）设备的处置

1. 按 §2 给该设备单独派一个清理 run。它不会立即执行，而是**停在准入等待**：设备一上线即被认领执行。
   由于 `device_host_map` 在准入评估时从 Device 表现取，且归属会随上线自愈，
   **插回任意一台 host 都会被正确认领**，无需要求现场插回原机位。
2. 通知现场：**上电后先别交付任何计划**。武装链路会自己重启若干轮；等 §1 的 uptime 连续增长两次以上
   成立，或等清理 run 的 `cleanup COMPLETED`，再交接。
3. 若现场无法上电（电池深放、线材/座子故障、长时间无心跳），按"疑似硬件/供电问题"另计，
   不要归因到开关机专项——判据：该设备**从未出现在**被中止 run 的 job 列表里，
   或掉线时刻早于专项启动时刻（实测 `…0143/…0157/…0150/…0158` 四台即属此类，掉线于 10-02/10-03，
   早于 10-12 启动的专项）。

## 4. 交付前的一行拦截（建议）

任何计划派发前，若目标设备满足「近 24h 内被 power-cycle 专项使用过 且 无 §1 的 `running=false` 回读记录」，
先补一次 §1/§2 再派。在 ADR-0061 D3 落地成平台强制之前，这条只能靠人执行。
