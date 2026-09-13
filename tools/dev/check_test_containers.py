#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""残留 testcontainer 巡检与安全清理（#1482；#1655 收紧判定与误删面）。

背景：``backend/tests`` 未设 ``TEST_DATABASE_URL`` 时由 conftest 起
per-process ``postgres:16`` 容器（隔离正确）；被 kill/超时的 pytest 进程会
遗留容器——本机实测累积 13 个（最老 2 天）。本工具只做**巡检与安全清理**：

- 默认 dry-run：列出相关容器与年龄，给出建议命令；
- 目标判定（#1655）：以 testcontainers 注入的 label（``org.testcontainers``）
  为准，其次 ``testcontainers/*`` 镜像——**不再**按裸 ``postgres:16`` 镜像名
  匹配，避免误伤开发机手工起、仍在使用的 PG；
- ``--prune --yes``：仅清理年龄 ≥ ``--min-age-minutes``（默认 120）的疑似
  残留；``--prune`` 不带 ``--yes`` 只列出待删清单（#1655：删除需显式确认）；
- ``--strict``：存在疑似残留时退出码 1；
- docker 不可用 / daemon 异常 → 退出码 2（不报绿，巡检不可用不等于无残留）。

用法::

    python tools/dev/check_test_containers.py
    python tools/dev/check_test_containers.py --strict --min-age-minutes 60
    python tools/dev/check_test_containers.py --prune --yes
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

# 目标容器：testcontainers 注入的 label 为权威判据；镜像名只作兜底
# （ryuk 等由 testcontainers 生态直接起的容器）。裸 `postgres:16` 不再匹配——
# 手工起的同名镜像可能是正在使用的业务实例（#1655）。
_TARGET_LABEL = "org.testcontainers"
_TARGET_IMAGE_MARKERS = ("testcontainers/ryuk", "testcontainers/")


@dataclass(frozen=True)
class Container:
    """一条 `docker inspect` 结果。"""

    cid: str
    name: str
    image: str
    created: datetime
    labels: dict[str, str] = field(default_factory=dict)

    def age_minutes(self, *, now: datetime | None = None) -> float:
        ref = now or datetime.now(timezone.utc)
        return (ref - self.created).total_seconds() / 60.0

    def is_target(self) -> bool:
        if self.labels.get(_TARGET_LABEL):
            return True
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


def _run_docker(args: list[str]) -> tuple[int, str]:
    """执行 docker 子命令，返回 (returncode, stdout)。

    #1655：此前只取 stdout、丢弃 rc —— daemon 未运行时 `docker ps` 返回空
    输出，巡检会误报「未发现 testcontainer」并绿退。rc 是「docker 是否可用」
    的唯一判据，必须上抛给调用方。
    """
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout


def _list_containers(runner) -> list[Container]:
    rc, raw = runner(["ps", "-q"])
    if rc != 0:
        raise RuntimeError(f"docker ps failed rc={rc}: {raw.strip()[:200]}")
    ids = [line.strip() for line in raw.splitlines() if line.strip()]
    if not ids:
        return []
    fmt = (
        "{{.Id}}\t{{.Name}}\t{{.Config.Image}}\t{{.Created}}"
        "\t{{json .Config.Labels}}"
    )
    rc, out = runner(["inspect", "--format", fmt, *ids])
    if rc != 0:
        raise RuntimeError(f"docker inspect failed rc={rc}: {out.strip()[:200]}")
    containers: list[Container] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        cid, name, image, created, labels_raw = (p.strip() for p in parts)
        try:
            created_dt = parse_docker_time(created)
        except ValueError:
            continue
        try:
            labels = json.loads(labels_raw) if labels_raw else {}
        except (TypeError, ValueError):
            labels = {}
        if not isinstance(labels, dict):
            labels = {}
        containers.append(Container(
            cid=cid[:12], name=name.lstrip("/"), image=image, created=created_dt,
            labels={str(k): str(v) for k, v in labels.items()},
        ))
    return containers


def main(
    argv: list[str] | None = None, *, runner=_run_docker,
    now: datetime | None = None,
) -> int:
    """``now`` 为判龄基准时钟（缺省=真实当前时间）。

    年龄判定与「当前时间」耦合，调用方（单测/巡检钩子）必须能固定时钟，
    否则判定结果随真实时间漂移——#1541 即测试冻结 NOW、实现取真实 now 的失配。
    """
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
    parser.add_argument(
        "--yes", action="store_true",
        help="与 --prune 连用：确认执行删除（缺省只列出待删清单，不删除）",
    )
    args = parser.parse_args(argv)

    try:
        containers = _list_containers(runner)
    except FileNotFoundError:
        print("docker 不可用：无法巡检 testcontainer（退出码 2）", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        # #1655：daemon 异常/调用失败 ≠ 无残留——不能报绿
        print(f"docker 巡检失败：{exc}（退出码 2）", file=sys.stderr)
        return 2

    stale, recent = plan_targets(
        containers, min_age_minutes=args.min_age_minutes, now=now,
    )

    if not stale and not recent:
        print("未发现 testcontainer（label org.testcontainers / ryuk）。")
        return 0

    print(f"目标容器：疑似残留 {len(stale)} 个，近期活跃 {len(recent)} 个")
    for c in sorted(stale, key=lambda x: x.created):
        print(f"  [残留 {c.age_minutes(now=now):.0f}m] {c.cid}  {c.name}  {c.image}")
    for c in sorted(recent, key=lambda x: x.created, reverse=True):
        print(f"  [活跃 {c.age_minutes(now=now):.0f}m] {c.cid}  {c.name}  {c.image}")

    if stale and (not args.prune or not args.yes):
        ids = " ".join(c.cid for c in stale)
        print("\n建议清理（dry-run）：")
        print(f"  docker rm -f {ids}")
        if args.prune and not args.yes:
            print("--prune 需显式确认：确认无活跃使用后加 --yes 才会执行删除。")
        else:
            print("或：python tools/dev/check_test_containers.py --prune --yes")

    # 清理后仍有残留 = 复核结果；None = 复核失败、结果不可知（#1714）
    remaining_stale: list[Container] | None = None
    if args.prune and args.yes and stale:
        rm_failed: list[str] = []
        for c in stale:
            rc, out = runner(["rm", "-f", c.cid])
            if rc != 0:
                rm_failed.append(c.cid)
                print(
                    f"  rm -f {c.cid} 失败(rc={rc}): {out.strip()[:160]}",
                    file=sys.stderr,
                )
        # 复核：再次列举，报告仍在的目标容器（rm 失败的会留在列表里）
        try:
            remaining_stale, _ = plan_targets(
                _list_containers(runner), min_age_minutes=args.min_age_minutes,
                now=now,
            )
        except Exception:  # noqa: BLE001 - 复核失败不掩盖清理动作
            # 不把「不可知」伪装成「已清干净」（#1714）：置 None 并在下方显式报告
            remaining_stale = None

        if remaining_stale is None:
            print(
                f"\n已执行 {len(stale)} 个删除；"
                "复核失败：清理结果不可知（无法再次列举容器）。"
            )
        else:
            print(
                f"\n已清理 {len(stale) - len(remaining_stale)} 个疑似残留容器"
                f"；仍有 {len(remaining_stale)} 个未清理。"
            )
        if rm_failed:
            print(f"（rm 调用失败 {len(rm_failed)} 个：{', '.join(rm_failed)}）")

    if args.strict:
        # strict 判定必须基于**复核后**的状态（#1714）：清理前有残留、清理后已空
        # 属成功，不应返回 1；复核失败（None）结果不可知，保守返回 1。
        if remaining_stale is None:
            return 1 if stale else 0
        if remaining_stale:
            return 1
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
