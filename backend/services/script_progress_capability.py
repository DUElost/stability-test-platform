"""脚本 PROGRESS 能力登记（#136：stall_seconds 配置门禁）。

停滞钟只认 stderr 上的 ``PROGRESS {"seq": N, ...}`` 戳；在脚本版本接入该
协议前给 PlanStep 打开 ``stall_seconds``，长静默段会被误杀。

这里维护"已知支持 PROGRESS 且无已知盲区缺陷"的版本白名单，作为 Plan
创建/更新时的配置契约校验：

- ``monkey_setup`` v2.3.0 虽实现协议，但 #138 的 push 回调缺陷到 v2.3.1
  才修复，因此从 v2.3.1 起才算可安全启用；
- ``flash_firmware`` v1.1.0 在 flash 阶段打戳（#134）。

白名单需要随新版本发布同步更新；脚本元数据方案（capabilities 字段）可作为
后续演进，当前以显式登记为准。
"""

PROGRESS_CAPABLE_SCRIPTS: set[tuple[str, str]] = {
    ("monkey_setup", "v2.3.1"),
    ("monkey_setup", "v2.3.2"),
    ("monkey_setup", "v2.3.3"),
    ("flash_firmware", "v1.1.0"),
}


def script_supports_progress(script_name: str, script_version: str) -> bool:
    """Return True if the script version is known to emit PROGRESS stamps."""
    return (script_name, script_version) in PROGRESS_CAPABLE_SCRIPTS
