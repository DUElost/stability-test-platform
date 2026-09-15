# #2010 展锐 uniview 复用同一 event_id 目录：重复异常在平台侧全部丢失

Status: implemented
Class: bug-fix

## Decision

展锐（UNISOC）的 `uniview` 对**同一类型异常的第 2、3… 次发生**，会把新 dump **追加进同一个
`event_id` 目录**，目录名**始终不变**。而 reconciler 的去重键是**目录名**（`_processed` 为名字集合），
于是「同名目录被更新」被当作「已处理」→ **永不重拉、永不发射**，重复异常在平台侧完全不可见。

真机证据（主机 `172.21.x.x` 直读设备）：

```text
/data/ylog/uniview_exception/JE.103000004/        ← 目录名与 event_id 始终不变
  001-2026-09-08_06-59-12.tar.gz                  原始事件（com.android.camera2）
  002-2026-09-14_22-31-50.tar.gz                  am crash com.android.settings
  003-2026-09-14_22-48-51.tar.gz                  am crash com.android.camera2
unievent_info: {"event_count":"3"}（三条 kick_datetime 如上）
```

而同时间平台侧 **无任何** 新 `device_log_event` / `job_log_signal`（观测 24h 亦然），
且平台持有的副本仍是首个事件（`size_bytes=2200885` ≈ `001-…tar.gz`，而目录实际已 6.6MB）。

修法：把「目录级去重」升级为「**目录级 + 内容签名**」——

- `_list_remote_uniview_root` 由 `ls -1`（只有名字）改为 `ls -l`，返回 `{name: signature}`，
  签名取行内**名字之前**的字段（size + mtime）；一次列举覆盖全部目录，**不增加 shell 调用**；
- `_processed` 由 `Set[str]` 变为 `Dict[str, Optional[str]]`（名字 → 签名）；
- **拉取侧**：`local 已就绪且签名未变` 才跳过；签名变化（或本地缺失）即重拉（staging 覆盖 ✓）；
- **发射侧**：仅「从未处理」或「远端签名变化」才发射，成功发射后记录新签名；
- 旧状态（`list`，只有名字）加载时归一为 `{name: None}`，首拍对这些目录**重新确认一次**，
  从而把历史上被吞掉的更新**补发**出来。

## Alternatives

- **按目录 mtime 直接重拉整棵目录**（每次 tick）：会在无变化时反复传输大目录（6MB+），故只按签名变化触发。
- **改用 `unievent_info` 的 `event_count` 作签名**：语义更直接，但需对**每个**目录额外发一次 shell 读取；
  当前签名（size+mtime）在**一次列举**里就能拿到，成本更低。已在 Revisit 记录可切换。
- **在中心侧去重（按 (job, signal_seq) 或 nfs_path）**：不能解决问题——信号从未产生，中心侧无从去重。
- **改由厂商侧新建目录**：不可控（厂商行为），故在平台侧适配。

## Verification

- 新增用例 `test_same_dir_with_new_content_is_repulled_and_reemitted`：
  第 1 拍发射 ✓；**第 2 拍签名未变 → 不重拉、不重发**（反例）✓；第 3 拍签名变化 → 重拉 + 重发 ✓；
- **双侧反例实证**：只破坏「发射侧」→ `assert 0 == 1` 转红；只破坏「拉取侧」→ `assert 1 == 1+1`
  （“签名变化却没有重拉”）转红；恢复后全绿；
- `backend/agent/tests/test_unisoc_reconciler.py` **16 passed**（含 14 条既有用例的协议同步：
  `ls -1`→`ls -l` 形状、`_processed` 由 set 改按键比较——均属**有意行为变更**）；
- `ruff` 全过。

**行为变化（有意，需评审知晓）**：旧格式状态（或签名未知）下，首拍会对已处理目录**重新确认一次**，
因此 `test_pruned_name_not_repulled` 的旧断言（“已处理⇒永不重拉”）按新契约更新；
同一崩溃的转储本身没有变化（签名稳定后不再重拉重发），故不会造成持续重复计数。

## Revisit

- **子事件粒度**：当前是「目录粒度」——同一目录新增 N 个子事件只发一条信号（元数据取**最后一条**
  事件行，即最新一次崩溃，已用真机三事件原文验证）。若产品需要“每个子事件一行”，应改为
  按 `(event_id, tar 文件名)` 或 `(event_id, kick_datetime, proc)` 生成信号，并同步调整
  `job_log_signal.path_on_device` 的语义（现为目录名，无法区分同目录内的多次）。
- **签名强度**：size+mtime（分钟粒度）在“同一分钟内连续两次追加”时可能漏判；若实测到该情形，
  应切换为 `unievent_info` 的 `event_count` 或目录内最新 `*.tar.gz` 名。
- **拉取重试**：拉取失败时签名不会落定，下一拍会重试（原实现是“已处理即放弃”）。这是有意的改进，
  但若某目录长期拉取失败会每拍重试一次，可考虑加退避。
