"""Ed25519 ``usbnet_grant`` signing, verification & cooldown contract (ADR-0066 D6.3 / ADR-0037 D10.4).

Token format (byte-compatible with ``stp-agent-priv usbnet-attach`` in B13-U3):
    ``base64url_nopad(canonical_json_utf8) + "." + base64url_nopad(raw_64byte_ed25519_sig)``
where the signature covers the ASCII bytes of the first segment.

Payload fields:
    ``{session_id, generation, host_id, device_serial, usb_devpath, egress, iat, exp}``
with ``300 <= (exp - iat) <= 1800`` (default ``900`` seconds).

Cooldown issuance contract (B13 v1.3 / ADR-0066 D6.3):
- ``iat`` is ALWAYS the control plane's truthful wall-clock ``int(time.time())``;
  this module NEVER forges a future ``iat`` to jump over ``min_allowed_iat_by_egress``.
- If ``iat <= min_allowed_iat_by_egress.get(egress, 0)``, signing refuses with
  :class:`UsbnetEgressCooldownError` carrying ``retry_after_seconds = barrier + 1 - iat``.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.core.settings.network_supply import get_network_supply_settings

GRANT_MIN_TTL_SECONDS = 300
GRANT_MAX_TTL_SECONDS = 1800
GRANT_DEFAULT_TTL_SECONDS = 900
GRANT_REQUIRED_FIELDS = (
    "session_id",
    "generation",
    "host_id",
    "device_serial",
    "usb_devpath",
    "egress",
    "iat",
    "exp",
)


class UsbnetGrantError(Exception):
    """Base error for ``usbnet_grant`` issuance/verification failures."""


class UsbnetGrantKeyNotConfiguredError(UsbnetGrantError):
    """Raised fail-closed when Ed25519 signing key is missing or invalid."""


class UsbnetEgressCooldownError(UsbnetGrantError):
    """Raised when ``iat <= host_barrier`` during a ``usbnet-teardown`` cooldown."""

    def __init__(
        self,
        *,
        host_id: str,
        egress: str,
        iat: int,
        min_allowed_iat: int,
    ) -> None:
        self.host_id = host_id
        self.egress = egress
        self.iat = int(iat)
        self.min_allowed_iat = int(min_allowed_iat)
        self.retry_after_seconds = max(1, self.min_allowed_iat + 1 - self.iat)
        super().__init__(
            f"usb_net egress {egress!r} on host {host_id!r} is in teardown cooldown "
            f"(iat={self.iat} <= min_allowed_iat={self.min_allowed_iat}); "
            f"retry after {self.retry_after_seconds}s"
        )

    def detail(self) -> dict[str, Any]:
        return {
            "code": "USBNET_EGRESS_COOLDOWN",
            "message": str(self),
            "host_id": self.host_id,
            "egress": self.egress,
            "iat": self.iat,
            "min_allowed_iat": self.min_allowed_iat,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass(frozen=True)
class SignedUsbnetGrant:
    token: str
    payload: dict[str, Any]

    @property
    def iat(self) -> int:
        return int(self.payload["iat"])

    @property
    def exp(self) -> int:
        return int(self.payload["exp"])


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    if not isinstance(segment, str) or not segment:
        raise UsbnetGrantError("empty base64url segment")
    pad = "=" * ((4 - len(segment) % 4) % 4)
    try:
        return base64.urlsafe_b64decode((segment + pad).encode("ascii"))
    except Exception as exc:
        raise UsbnetGrantError(f"invalid base64url encoding: {exc}") from exc


def load_ed25519_private_key(raw_key: str | None = None) -> Ed25519PrivateKey:
    """Load Ed25519 private key from PEM, 64-hex raw seed, or base64/base64url raw seed.

    Fail-closed: raises :class:`UsbnetGrantKeyNotConfiguredError` if missing or invalid.
    """
    value = raw_key if raw_key is not None else get_network_supply_settings().stp_usbnet_grant_signing_key
    text = (value or "").strip()
    if not text:
        raise UsbnetGrantKeyNotConfiguredError(
            "STP_USBNET_GRANT_SIGNING_KEY is not configured"
        )
    # Allow literal \n escapes in env strings for PEM keys.
    if "BEGIN" in text and "\\n" in text:
        text = text.replace("\\n", "\n")

    if text.startswith("-----BEGIN"):
        try:
            loaded = serialization.load_pem_private_key(
                text.encode("utf-8"), password=None
            )
        except Exception as exc:
            raise UsbnetGrantKeyNotConfiguredError(
                f"invalid PEM Ed25519 signing key: {exc}"
            ) from exc
        if not isinstance(loaded, Ed25519PrivateKey):
            raise UsbnetGrantKeyNotConfiguredError(
                "STP_USBNET_GRANT_SIGNING_KEY must be an Ed25519 private key"
            )
        return loaded

    # 64-char hex raw 32-byte seed
    if len(text) == 64:
        try:
            seed = bytes.fromhex(text)
            return Ed25519PrivateKey.from_private_bytes(seed)
        except ValueError:
            pass

    # base64url or standard base64 raw 32-byte seed
    pad = "=" * ((4 - len(text) % 4) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decoder((text + pad).encode("ascii"))
            if len(raw) == 32:
                return Ed25519PrivateKey.from_private_bytes(raw)
        except Exception:
            continue

    raise UsbnetGrantKeyNotConfiguredError(
        "STP_USBNET_GRANT_SIGNING_KEY is not a valid Ed25519 PEM/hex/base64 private key"
    )


def load_ed25519_public_key(raw_key: str) -> Ed25519PublicKey:
    """Load Ed25519 public key from PEM, 64-hex, or base64/base64url raw 32 bytes."""
    text = (raw_key or "").strip()
    if not text:
        raise UsbnetGrantError("empty Ed25519 public key")
    if "BEGIN" in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    if text.startswith("-----BEGIN"):
        loaded = serialization.load_pem_public_key(text.encode("utf-8"))
        if not isinstance(loaded, Ed25519PublicKey):
            raise UsbnetGrantError("public key is not Ed25519")
        return loaded
    if len(text) == 64:
        try:
            return Ed25519PublicKey.from_public_bytes(bytes.fromhex(text))
        except ValueError:
            pass
    pad = "=" * ((4 - len(text) % 4) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decoder((text + pad).encode("ascii"))
            if len(raw) == 32:
                return Ed25519PublicKey.from_public_bytes(raw)
        except Exception:
            continue
    raise UsbnetGrantError("invalid Ed25519 public key encoding")


def normalize_min_allowed_iat_by_egress(
    raw: Mapping[str, Any] | None,
) -> dict[str, int]:
    """Normalize ``{egress: int}`` cooldown map (missing keys default to 0)."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        egress = str(key).strip()
        if not egress:
            continue
        try:
            ts = int(value)
        except (TypeError, ValueError):
            continue
        if ts >= 0:
            out[egress] = ts
    return out


def merge_min_allowed_iat_by_egress(
    existing: Mapping[str, Any] | None,
    reported: Mapping[str, Any] | None,
) -> dict[str, int]:
    """Monotonically merge reported cooldown barriers (``max(cur, val)`` per egress)."""
    merged = normalize_min_allowed_iat_by_egress(existing)
    for egress, ts in normalize_min_allowed_iat_by_egress(reported).items():
        merged[egress] = max(merged.get(egress, 0), ts)
    return merged


def egress_cooldown_barrier(
    min_allowed_iat_by_egress: Mapping[str, Any] | None,
    egress: str,
) -> int:
    """Return the host's ``min_allowed_iat`` barrier for ``egress`` (missing → 0)."""
    normalized = normalize_min_allowed_iat_by_egress(min_allowed_iat_by_egress)
    return int(normalized.get(egress, 0))


def check_egress_cooldown(
    *,
    host_id: str,
    egress: str,
    min_allowed_iat_by_egress: Mapping[str, Any] | None,
    now_ts: int | float | None = None,
) -> int:
    """Return truthful wall-clock ``iat`` or raise :class:`UsbnetEgressCooldownError`.

    NEVER forges a future ``iat``: strictly checks ``iat > barrier``.
    """
    iat = int(time.time() if now_ts is None else now_ts)
    barrier = egress_cooldown_barrier(min_allowed_iat_by_egress, egress)
    if iat <= barrier:
        raise UsbnetEgressCooldownError(
            host_id=host_id,
            egress=egress,
            iat=iat,
            min_allowed_iat=barrier,
        )
    return iat


def sign_usbnet_grant(
    *,
    session_id: int,
    generation: int,
    host_id: str,
    device_serial: str,
    usb_devpath: str,
    egress: str,
    min_allowed_iat_by_egress: Mapping[str, Any] | None = None,
    ttl_seconds: int | None = None,
    now_ts: int | float | None = None,
    private_key: Ed25519PrivateKey | None = None,
) -> SignedUsbnetGrant:
    """Sign a short-lived Ed25519 ``usbnet_grant`` with truthful wall-clock ``iat``.

    Checks cooldown barrier first so callers receive 503 ``USBNET_EGRESS_COOLDOWN``
    with ``Retry-After`` instead of a doomed or forged-iat grant.
    """
    if not host_id or not device_serial or not usb_devpath or not egress:
        raise UsbnetGrantError(
            "session_id, generation, host_id, device_serial, usb_devpath and egress are required"
        )
    if int(session_id) <= 0 or int(generation) <= 0:
        raise UsbnetGrantError("session_id and generation must be positive integers")

    settings = get_network_supply_settings()
    ttl = settings.stp_usbnet_grant_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
    if ttl < GRANT_MIN_TTL_SECONDS or ttl > GRANT_MAX_TTL_SECONDS:
        raise UsbnetGrantError(
            f"usbnet_grant ttl_seconds={ttl} out of allowed range "
            f"[{GRANT_MIN_TTL_SECONDS}, {GRANT_MAX_TTL_SECONDS}]"
        )

    iat = check_egress_cooldown(
        host_id=host_id,
        egress=egress,
        min_allowed_iat_by_egress=min_allowed_iat_by_egress,
        now_ts=now_ts,
    )
    key = private_key if private_key is not None else load_ed25519_private_key()

    payload: dict[str, Any] = {
        "session_id": int(session_id),
        "generation": int(generation),
        "host_id": str(host_id),
        "device_serial": str(device_serial),
        "usb_devpath": str(usb_devpath),
        "egress": str(egress),
        "iat": int(iat),
        "exp": int(iat + ttl),
    }
    canonical = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    payload_b64 = _b64url_encode(canonical)
    signature = key.sign(payload_b64.encode("ascii"))
    if len(signature) != 64:
        raise UsbnetGrantError("unexpected Ed25519 signature length")
    sig_b64 = _b64url_encode(signature)
    return SignedUsbnetGrant(token=f"{payload_b64}.{sig_b64}", payload=payload)


def verify_usbnet_grant(
    token: str,
    public_key: Ed25519PublicKey | str,
    *,
    now_ts: int | float | None = None,
) -> dict[str, Any]:
    """Verify Ed25519 signature, schema, and TTL bounds of a ``usbnet_grant``."""
    if not isinstance(token, str) or token.count(".") != 1:
        raise UsbnetGrantError("malformed usbnet_grant token")
    payload_b64, sig_b64 = token.split(".", 1)
    sig_bytes = _b64url_decode(sig_b64)
    if len(sig_bytes) != 64:
        raise UsbnetGrantError("invalid Ed25519 signature length")

    pub = (
        public_key
        if isinstance(public_key, Ed25519PublicKey)
        else load_ed25519_public_key(public_key)
    )
    try:
        pub.verify(sig_bytes, payload_b64.encode("ascii"))
    except InvalidSignature as exc:
        raise UsbnetGrantError("invalid usbnet_grant signature") from exc

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise UsbnetGrantError(f"invalid usbnet_grant payload json: {exc}") from exc
    if not isinstance(payload, dict):
        raise UsbnetGrantError("usbnet_grant payload must be a JSON object")

    missing = [k for k in GRANT_REQUIRED_FIELDS if k not in payload]
    if missing:
        raise UsbnetGrantError(f"usbnet_grant missing fields: {missing}")

    iat = int(payload["iat"])
    exp = int(payload["exp"])
    ttl = exp - iat
    if ttl < GRANT_MIN_TTL_SECONDS or ttl > GRANT_MAX_TTL_SECONDS:
        raise UsbnetGrantError(f"usbnet_grant ttl out of range: {ttl}")

    if now_ts is not None:
        now_i = int(now_ts)
        if now_i >= exp:
            raise UsbnetGrantError("usbnet_grant expired")
        if iat > now_i + 30:
            raise UsbnetGrantError("usbnet_grant iat is too far in the future")

    return payload
