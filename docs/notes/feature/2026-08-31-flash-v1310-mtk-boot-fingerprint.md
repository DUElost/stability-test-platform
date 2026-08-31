# flash_firmware v1.3.10：MTK-only boot 稳定指纹

## 决定了什么

- 以 v1.3.9 为底新建 **v1.3.10**（不修改已发布 v1.3.7–v1.3.9 目录），移植 stash 补丁中的三项增量：
  1. `_usb_topology_fingerprint` 只取 MTK 口（vid `0e8d`）；
  2. `_wait_boot_stable` 首轮记基线 + 空指纹不误判；
  3. PROGRESS `done` 打在 boot-stabilize **之后**。
- Alembic `j0k1l2m3n4o5` 播种 v1.3.10，停用至 v1.3.8；v1.3.9 作回滚路径。

## 放弃的备选

- **直接改 v1.3.7 + migration sha 重锚**：违反 ADR-0020，且生产已升到 v1.3.9。
- **继续用整棵 USB 树指纹**：多机 host 上非 MTK 抖动会无限重置稳定窗口（.68 串行 14 台约 +21min）。

## 如何验证

```bash
python -m pytest backend/agent/tests/test_flash_firmware_v1310.py -q
```

合入后：迁移 → `POST /scripts/scan` → Plan 指 v1.3.10 → .68 串行首刷观察 boot 稳定窗口是否不再打满 max_wait。

## 何时重议

- MTK 口枚举规则变化（新 pid 族 / 非 0e8d 平台）需同步 `_list_mtk_ports` 与指纹判据。
- 若下游强依赖 `done` 在 verify 后立即出现，需改 Plan 解析而非回退顺序。
