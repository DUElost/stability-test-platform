#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""残留 testcontainer 巡检与安全清理（#1482）。

背景：``backend/tests`` 未设 ``TEST_DATABASE_URL`` 时由 conftest 起
per-process ``postgres:16`` 容器（隔离正确）；被 kill/超时的 pytest 进程会
遗留容器——本机实测累积 13 个（最老 2 天）。本工具只做**巡检与安全清理**：

- 默认 dry-run：列出相关容器（``postgres:16`` / ``testcontainers|ryuk``）
  与年龄，给出建议命令；
- ``--prune``：仅清理年龄 ≥ ``--min-age-minutes``（默认 120）的疑似残留，
  避免误删其他会话正在使用的实例；
- ``--strict``：存在疑似残留时退出码 1（供手动巡检或收尾钩子使用）。

用法::

    python tools/dev/check_test_containers.py
    python tools/dev/check_test_containers.py --strict --min-age-minutes 60
    python tools/dev/check_test_containers.py --prune
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

# 目标容器：testcontainers 起的 PG 与 reaper（ryuk）
_TARGET_IMAGE_MARKERS = ("postgres:16", "testcontainers/ryuk", "testcontainers")


@dataclass(frozen=True)
class Container:
    """一条 `docker inspect` 结果。"""

    cid: str
    name: str
    image: str
    created: datetime

    def age_minutes(self, *, now: datetime | None = None) -> float:
        ref = now or datetime.now(timezone.utc)
        return (ref - self.created).total_seconds() / 60.0

    def is_target(self) -> bool:
        image = self.image.lower()
        return any(m in image for m in _TARGET_IMAGE_MARKERS)


def parse_docker_time(raw: str) -> datetime:
    """解析 ``docker inspect`` 的 RFC3339（纳秒分数截到微秒）。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty docker timestamp")
    # python fromisoformat 最多接受 6 位小数；截断纳秒
    if "." in text:
        head, _, tail = text.partition(".")
        frac_digits = "".join(ch for ch in tail if ch.isdigit())
        tz_part = tail[len(frac_digits):]
        text = f"{head}.{frac_digits[:6]}{tz_part}"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def plan_targets(
    containers: list[Container], *, min_age_minutes: float, now: datetime | None = None,
) -> tuple[list[Container], list[Container]]:
    """返回 (疑似残留, 近期活跃) —— 只看目标镜像。"""
    stale: list[Container] = []
    recent: list[Container] = []
    for c in containers:
        if not c.is_target():
            continue
        (stale if c.age_minutes(now=now) >= min_age_minutes else recent).append(c)
    return stale, recent


def _run_docker(args: list[str]) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False,
    ).stdout


def _list_containers(runner) -> list[Container]:
    raw = runner(["ps", "-q"])
    ids = [line.strip() for line in raw.splitlines() if line.strip()]
    if not ids:
        return []
    fmt = "{{.Id}}\t{{.Name}}\t{{.Config.Image}}\t{{.Created}}"
    out = runner(["inspect", "--format", fmt, *ids])
    containers: list[Container] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        cid, name, image, created = (p.strip() for p in parts)
        try:
            created_dt = parse_docker_time(created)
        except ValueError:
            continue
        containers.append(Container(
            cid=cid[:12], name=name.lstrip("/"), image=image, created=created_dt,
        ))
    return containers


def main(argv: list[str] | None = None, *, runner=_run_docker) -> int:
    parser = argparse.ArgumentParser(
        description="巡检/清理残留的 testcontainer（默认 dry-run）",
    )
    parser.add_argument("--prune", action="store_true", help="执行清理（默认只报告）")
    parser.add_argument(
        "--min-age-minutes", type=float, default=120.0,
        help="判定为残留的最小年龄（分钟，默认 120；仅对 ≥ 该年龄的容器清理）",
    )
    parser.add_argument(
        "--strict", action="store_true", help="存在疑似残留时退出码 1",
    )
    args = parser.parse_args(argv)

    try:
        containers = _list_containers(runner)
    except FileNotFoundError:
        print("docker 不可用：无法巡检 testcontainer（退出码 2）", file=sys.stderr)
        return 2

    stale, recent = plan_targets(
        containers, min_age_minutes=args.min_age_minutes,
    )

    if not stale and not recent:
        print("未发现 testcontainer（postgres:16 / ryuk）。")
        return 0

    print(f"目标容器：疑似残留 {len(stale)} 个，近期活跃 {len(recent)} 个")
    for c in sorted(stale, key=lambda x: x.created):
        print(f"  [残留 {c.age_minutes():.0f}m] {c.cid}  {c.name}  {c.image}")
    for c in sorted(recent, key=lambda x: x.created, reverse=True):
        print(f"  [活跃 {c.age_minutes():.0f}m] {c.cid}  {c.name}  {c.image}")

    if stale and not args.prune:
        ids = " ".join(c.cid for c in stale)
        print("\n建议清理（dry-run；确认无活跃使用后加 --prune）：")
        print(f"  docker rm -f {ids}")
        print("或：python tools/dev/check_test_containers.py --prune")

    if args.prune and stale:
        for c in stale:
            runner(["rm", "-f", c.cid])
        # 复核：再次列举，报告仍在的目标容器（rm 失败的会留在列表里）
        try:
            remaining_stale, _ = plan_targets(
                _list_containers(runner), min_age_minutes=args.min_age_minutes,
            )
        except Exception:  # noqa: BLE001 - 复核失败不掩盖清理动作
            remaining_stale = []
            print("（复核失败：无法再次列举容器）")
        print(
            f"\n已清理 {len(stale) - len(remaining_stale)} 个疑似残留容器"
            f"；仍有 {len(remaining_stale)} 个未清理。"
        )

    if args.strict and stale:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
