"""R02-D3（#903）：单一 token 校验面。

REST（``get_current_user``）、metrics Bearer、Socket.IO dashboard 握手三个
消费面必须收敛到本模块的校验函数——此前 metrics/socket 只做签名级
``decode_token``，停用/删除用户的 token 到 exp 前全通（#903）。

校验链（任一步失败返回 None）：签名+type+exp → ``sub`` 解析为用户 PK →
查库 → ``is_active=="Y"`` → 会话纪元 ``ver`` 比对（R02-D2，#902：改密/
重置/停用/改角色即 bump，全部在发 token 立即失效）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.core.security import decode_token
from backend.models.user import User


def resolve_payload_user(db: Session, payload: dict) -> User | None:
    """按已解码 payload 解析有效用户；无效返回 None。

    ``ver`` 缺失（本部署前签发的存量 token）或不等（bump 后）一律拒绝——
    硬切换无宽限（设计 note §3）。"""
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.is_active != "Y":
        return None
    try:
        ver = int(payload.get("ver"))
    except (TypeError, ValueError):
        return None
    if ver != int(user.token_version):
        return None
    return user


def authenticate_token(db: Session, token: str, *, expected_type: str) -> User | None:
    """完整校验面：decode + ``resolve_payload_user``。"""
    payload = decode_token(token, expected_type=expected_type)
    if not payload:
        return None
    return resolve_payload_user(db, payload)
