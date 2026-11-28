# B13 U3：stp-agent-priv `usbnet-*` 受限提权通道、三层隔离与边界测试

Status: implemented
Class: feature

## Decision

按批次 B13（`#4212` v1.3）与 ADR-0037 D10 / ADR-0066 D6 落地主机侧 USB 反向供网受限提权通道（合入默认静默关闭）：

1. **六个窄子命令与解析边界（`backend/agent/stp_agent_priv.py`）**：新增 `usbnet-ensure-bridge`、`usbnet-attach`、`usbnet-detach`、`usbnet-revoke`、`usbnet-teardown` 与 `usbnet-status`，全部子解析器启用 `allow_abbrev=False`、校验 root UID=0、拒绝任意网桥名/CIDR/上联网卡/DNS/防火墙表达式；`capabilities` 输出新增 `usbnet` 与 `usbnet/grant-v1`。
2. **Root 属主授权配置 `/etc/stp-usbnet.conf` 与禁用态 fail-closed**：校验 `/etc` 父目录与配置文件非 symlink、`root:root`、非 group/other 可写、`<= 64 KiB` 与完整 JSON schema；缺失/异常返回 exit `2` + `STP_AGENT_PRIV_ERROR`，`usbnet_enabled != true` 对全部六个子命令（含 `usbnet-status`）一律返回 exit `2` + `STP_USBNET_DISABLED` 且不修改任何系统状态。
3. **纯 Python RFC 8032 Ed25519 验签与 `usbnet_grant` 规范格式**：保持 `stp_agent_priv.py` 零三方依赖（仅新增 stdlib `fcntl`/`hashlib`/`ipaddress`），支持 Ed25519 SPKI PEM 与 32 字节 raw base64url/base64 公钥；`usbnet_grant` 采用 `<base64url_nopad(json_utf8)>.<base64url_nopad(ed25519_sig)>`（签名为首段 ASCII 字节，载荷固定含 `session_id`/`generation`/`host_id`/`device_serial`/`usb_devpath`/`egress`/`iat`/`exp`），强制校验 `now < exp`、`iat <= now + 30`、`300 <= exp - iat <= 1800`、`host_id` 与 `allowed_egresses`。
4. **Root 属主原子台账 `/run/stp-agent-priv/usbnet-bindings.json`**：`0700` 目录 + `0600` 台账 + `flock` 排他锁 + 同目录 `fsync` + `os.replace` 原子写；维护 `active_bindings`（`attaching`/`attached`，强约束 `usb_devpath`/`iface` 单口唯一占用，满额时允许同一 `(session_id, generation)` 幂等重入）与 `revocation_state`（`revoked_sessions` 有界裁剪、`min_allowed_generation_by_serial` **仅按 `device_serial` 索引**、`min_allowed_iat_by_egress` 冷却屏障，缺席键默认 `0`）。
5. **可执行 sysfs 身份核验与隔离前置两阶段提交**：仅对 `/sys/class/net/<iface>`、`<net_real>/device`、`<usb_intf_real>/driver`（`{rndis_host, cdc_ncm}`）三处内核符号链接调用 `realpath` 并排除 `/sys/devices/virtual/` 及宿主机默认路由上联网卡，叶子属性以 `O_NOFOLLOW` + `S_ISREG` 有界读取；`usbnet-attach` 严格按 `attaching → down → master → isolated on + 回读 brport/isolated == "1" → 复核 ifindex & isolated → up → attached` 执行，失败先 `down` 再尽力 `nomaster` 并返回 exit `3`（`STP_USBNET_ATTACH_ROLLBACK`）。
6. **防接口名复用 `usbnet-detach`、`usbnet-revoke`、零残留 `usbnet-teardown` 与三层隔离**：`usbnet-detach` 实现完备三分支（`already_gone_or_reused` / `detached_unbridged` / `detached`），绝不触碰同名新接口或非本受控他桥成员；`usbnet-revoke` 保留未摘除 `active_bindings`；`usbnet-teardown` 在无活跃绑定或 `--force` 下无条件先原子持久化 `min_allowed_iat_by_egress = max(cur, now + 30)` 再清理并做零残留复核；`usbnet-ensure-bridge` 通过专属链 `STP_USBNET_{IN,FWD,NAT}_<EGRESS>` 与 `dnsmasq --port=0 --bind-interfaces` 落实三层默认拒绝隔离。
7. **Ansible 默认禁用下发模板（`tools/ansible/playbooks/update_agent.yml`）**：新增 opt-in（`agent_configure_usbnet=false`）任务下发默认禁用 `/etc/stp-usbnet.conf`（`root:root` `0640`）与 NetworkManager 防接管配置，不新增或修改任何 sudoers 行。

## Alternatives

- 调用 `cryptography` 第三方库或外部 `openssl pkeyutl` 子进程验签：前者破坏 `stp_agent_priv.py` 系统 python3 零三方依赖不变量（`test_wrapper_is_standalone_system_python_script`），后者引入子进程与临时密钥文件风险及 OpenSSL 1.1/3.x 参数差异；纯 Python RFC 8032 Ed25519 验签（~80 行）无外部依赖、可与 `cryptography` 及 RFC 8032 向量交叉验证。
- 将 `min_allowed_generation_by_serial` 同时按 `usb_devpath` 索引：否决——`lease_generation` 按设备单调递增，跨设备比较会误拦同物理口换入的低代次新设备（遵循 ADR-0037 D10.3）。
- 直接在 `INPUT`/`FORWARD` 默认链追加匿名规则：否决——难以保证 `usbnet-teardown` 精确零残留清理且存在误伤宿主机/Docker 规则风险，改用 `STP_USBNET_{IN,FWD,NAT}_<EGRESS>` 专属规则链。

## Verification

- `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python scripts/run_pytest.py tests/test_agent_priv_usbnet.py tests/test_agent_priv_parser_contract.py tests/test_agent_priv_boundary.py tests/test_agent_priv_usb_power.py tests/test_agent_priv_xhci_rebind.py -q`
- `.venv/bin/python tools/dev/check-internal-ip-leak.py --check`
- `.venv/bin/python tools/dev/check_governance_surface.py --base origin/main`
- `.venv/bin/python -m ruff check backend tools scripts tests`
- `.venv/bin/python -m compileall -q backend tools scripts`
- `git diff --cached --check`

## Revisit

- 待 U0 PR #4213 合入 `main` 后，随 U3 同步或紧邻跟进将 ADR-0037 §4 不变量第 2、3 条转正登记 `usbnet-*` 强控制与 `tests/test_agent_priv_usbnet.py`（本 PR 按批次协调边界未触碰 U0 独占文档文件）。
- U1（`network_grant.py`）与 U4（Agent `usb_net` 运行时）按本 Note 冻结的 `<base64url_nopad(json_utf8)>.<base64url_nopad(ed25519_sig)>` 令牌契约与 `usbnet-status` JSON 形态对接。
- 试点主机（1 → 2 → 按需扩展）经 Ansible 下发新版 wrapper 与 `/etc/stp-usbnet.conf` 后完成三层隔离真机红队验收。
