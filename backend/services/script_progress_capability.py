"""脚本 PROGRESS 能力校验（#136：stall_seconds 配置门禁）。

停滞钟只认 stderr 上的 ``PROGRESS {"seq": N, ...}`` 戳；在脚本版本接入该
协议前给 PlanStep 打开 ``stall_seconds``，长静默段会被误杀。

能力来源是 ``script.capabilities`` 列，由 ``scan_script_root`` 从版本目录的
``capabilities.json`` 登记（#171）。不再维护控制面硬编码白名单：

- ``monkey_setup`` v2.3.0 虽实现协议，但 #138 的 push 回调缺陷到 v2.3.1
  才修复，因此其版本目录不声明 ``progress_stamps``，从 v2.3.1 起才声明；
- ``flash_firmware`` v1.1.0 在 flash 阶段打戳（#134），同样在目录声明。

新版本接入 PROGRESS 后，只需在版本目录添加
``capabilities.json: {"capabilities": ["progress_stamps"]}`` 并重新 scan，
控制面代码无需改动。
"""

from __future__ import annotations

from sqlalchemy import select

from backend.models.script import Script

PROGRESS_CAPABILITY = "progress_stamps"


def script_supports_progress(db, script_name: str, script_version: str) -> bool:
    """Return True if the active script row declares ``progress_stamps``.

    版本号规范化（去/加 ``v`` 前缀两种都查）：plan_step 存储与 API 载荷用无
    ``v``（``2.3.3``），脚本目录与部分测试用带 ``v``（``v2.3.3``）。
    """
    normalized = script_version.lstrip("v")
    candidates = [normalized, f"v{normalized}"]
    row = db.execute(
        select(Script.capabilities).where(
            Script.name == script_name,
            Script.version.in_(candidates),
            Script.is_active.is_(True),
        ).limit(1)
    ).first()
    if row is None or not row[0]:
        return False
    return PROGRESS_CAPABILITY in row[0]
