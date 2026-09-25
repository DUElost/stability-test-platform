"""纯客户端 IP 解析（可信代理 + X-Forwarded-For）。#3297 自 core.limiter 拆出。

为什么留在 core：`resolve_client_ip` 是零框架依赖的网络原语，两侧都需要——
`backend/api/middleware/limiter.py`（限流分桶）与 `backend/services/audit_writer.py`
（审计 IP）。写入侧在 services 层，够不着 api 层（C1 禁止 services→api 上引），
所以它必须留在最底层的 core；limiter 的中间件与 RateLimiter 属 HTTP 面，
已迁 `backend/api/middleware/limiter.py`。

客户端 IP 的取法(#81):
  只有当**直连对端**在可信代理白名单里时才采信 `X-Forwarded-For`,并从
  **右往左**取第一个非可信条目。nginx 用 `$proxy_add_x_forwarded_for`
  (= 已有 XFF + `$remote_addr`),所以最右侧才是它实际观察到的客户端;
  取最左侧仍可被伪造 —— 客户端自带 `X-Forwarded-For: fake`,经 nginx 后
  变成 `fake, <真实IP>`,最左侧就是攻击者写的值。

  默认白名单 = loopback,只覆盖 **systemd 部署**:那里 uvicorn 绑
  127.0.0.1,nginx 同机反代,对端就是 127.0.0.1。

  **其它拓扑必须显式配置 STP_TRUSTED_PROXIES**,默认值不够:
  - docker-compose:容器内 uvicorn 绑 0.0.0.0,nginx 走 Docker 网络访问
    `server:8000`,对端是 172.x 容器地址 → 不配的话 XFF 全被忽略,所有用户
    挤进同一个桶、互相把对方限流掉。compose 文件里已设好。
  - 独立 LB / 多层代理:把每一层的网段都列进来。
  故意不把 172.16.0.0/12 塞进全局默认 —— 那会让任何部署在私有网段的
  非代理主机都获得伪造 XFF 的能力。
"""
import ipaddress
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128"

UNKNOWN_CLIENT = "unknown"


def _parse_networks(raw: str) -> Tuple[ipaddress._BaseNetwork, ...]:
    nets = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            logger.warning("invalid_trusted_proxy_entry: %r (ignored)", token)
    return tuple(nets)


def get_trusted_proxies() -> Tuple[ipaddress._BaseNetwork, ...]:
    """可信代理网段。默认仅 loopback。

    设为空字符串 = 完全不信任 XFF(直接暴露在公网、没有反代时的正确选择)。
    """
    raw = os.getenv("STP_TRUSTED_PROXIES")
    if raw is None:
        raw = DEFAULT_TRUSTED_PROXIES
    return _parse_networks(raw)


def _is_trusted(ip_str: str, trusted: Tuple[ipaddress._BaseNetwork, ...]) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in trusted)


def resolve_client_ip(
    peer: Optional[str],
    forwarded_for: Optional[str],
    trusted: Optional[Tuple[ipaddress._BaseNetwork, ...]] = None,
) -> str:
    """定位真实客户端 IP。

    对端不可信 → 直接用对端,**完全忽略** XFF(否则任何人都能改限流桶)。
    对端可信   → 从右往左找第一个非可信条目;全是可信代理时退回对端。
    """
    if trusted is None:
        trusted = get_trusted_proxies()

    if not peer:
        return UNKNOWN_CLIENT
    if not _is_trusted(peer, trusted):
        return peer

    for hop in reversed([h.strip() for h in (forwarded_for or "").split(",")]):
        if not hop:
            continue
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            # 非法条目(伪造/畸形)不能当作客户端标识
            continue
        if not _is_trusted(hop, trusted):
            return hop
    return peer
