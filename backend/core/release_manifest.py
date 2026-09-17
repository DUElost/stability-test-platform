"""运行时版本真值：读部署树里的 ``release-manifest.json``（#2341）。

**为什么不是常量**：控制面此前把版本写死在 ``backend/main.py``（``"2.0.0"``），
``stability_build_info`` 因此与站点实际跑的 revision 无关——Build Version 面板永远
显示 2.0.0。这是「有面板、数据是假的」，比没数据更难发现（#2286 的 Revisit 拆出本单；
#2276「绿色安装旧版本」正是这类问题的实证）。

**真值一直在同一棵树里**：``tools/release/build_bundle.py`` 产出的清单
（``product.version`` / ``source.revision``），安装链本身也以它做 fail-closed 校验
（``tools/site_config/install.py``：``manifest.product.version != expected_release`` 即阻断）。
运行时只读它，不新增环境变量（新增会牵动 8 个 ``.env*.example`` 与
``tests/test_env_example_parity.py`` 门禁，而清单已是既有事实源）。

**裁决（#2341 + #2572）**：真值来源优先级 = 清单 > checkout(git) > 显式 ``unknown``；
**不**回落 ``backend.__version__``（那是第二处会漂移的字面量，已删除），也**不**回落
任何具体版本号。

**#2572 为什么加了第三档**：清单只存在于**发布物**里——站点安装会把清单落到部署树
根（#2572 补的 s2 落地），而本机生产控制面是 **git checkout** 形态（unit 的
``WorkingDirectory`` = 仓根，见 ``deploy/control-plane/systemd/``），那棵树里**本就没有**
清单 ⇒ ``resolve_build_info()`` 恒 ``unknown``，而 ``unknown`` 无论怎么渲染都说不出
「跑的到底是哪个 revision」。checkout 形态的真值就是 git 自己。

**为什么在读取侧回落，而不是让部署脚本写一份清单**：写侧多一处会**静默过期**的副本
（``git pull`` 后忘记重新生成 → 清单报旧 revision，比 ``unknown`` 更坏——又变成
「有面板、数据是假的」）。读取侧读到的是当次启动时那棵树的真实状态；站点形态没有
``.git``，探测失败即回到 ``unknown``，原语义不变。

**不带 ``database.schema_target`` label**：它是**安装期声明值**，与 ``/health`` 的
``alembic_head``（运行期实测）语义不同，加 label 等于造第三个事实源。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

#: 部署树根 = 仓根（服务单元从仓根启动 uvicorn）；与 ``core/schema_revision.py``
#: 的 ``_REPO_ROOT`` 同款写法（那里是运行时定位仓库根的既有先例）。
_REPO_ROOT = Path(__file__).resolve().parents[2]

MANIFEST_NAME = "release-manifest.json"

#: 读不到清单时的回落值。**必须显式**且不含任何具体版本号——回落成 "2.0.0" 之类
#: 又会造一个「看起来有答案」的假信息，正是本单要消灭的形态（#2341 验收判据）。
UNKNOWN = "unknown"

#: checkout 形态（无清单）的 version 值。**不**从 tag 派生：本仓的 tag（``baseline-*``）
#: 不是产品版本，照着拼一个「看起来像版本」的串同样是假信息。``-dirty`` 后缀表示
#: tracked 文件相对 HEAD 有未提交改动——「跑的代码 ≠ 该 revision」必须看得见，因为
#: 本机生产控制面与开发工作树就是同一棵树。
CHECKOUT_VERSION = "checkout"
CHECKOUT_DIRTY_VERSION = "checkout-dirty"

#: git 探测超时（秒）：启动路径上的外部命令，卡住比没有版本更糟。
_GIT_TIMEOUT_SECONDS = 5


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _git(repo_root: Path, *args: str) -> str:
    """跑一次 ``git -C <repo_root> ...`` 并返回 stdout；任何失败都返回空串，不抛。

    非 git 工作树（站点安装的部署树）走的就是「失败即空串」这一档：没有 ``.git``
    时 ``rev-parse`` 退出非零，调用方据此判定「不是 checkout」。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # 无 git 可执行文件 / 超时 / 权限：都算「探测不到」，不是致命错误。
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_checkout_info(repo_root: Path) -> Optional[Tuple[str, str]]:
    """checkout 形态的 ``(version, commit)``；不是 git 工作树则 ``None``。

    ``dirty`` 只算 **tracked** 改动，判据与部署源守卫
    ``tools/dev/check-deploy-source.sh`` 同款（那里把 tracked 脏工作树当硬条件）；
    未跟踪文件不参与——它们不是「与 HEAD 不同的已发布代码」。
    """
    revision = _git(repo_root, "rev-parse", "HEAD")
    # 与 build_bundle._revision 同款的 sha 形状校验：非 sha 输出（git 不在、非仓、
    # 输出被污染）一律视为「探测不到」。
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision or ""):
        return None
    dirty = bool(_git(repo_root, "status", "--porcelain", "--untracked-files=no"))
    return (CHECKOUT_DIRTY_VERSION if dirty else CHECKOUT_VERSION, revision)


def resolve_build_info(
    manifest_path: Optional[Path] = None, repo_root: Optional[Path] = None
) -> Tuple[str, str]:
    """返回 ``(version, commit)``：清单 > checkout(git) > ``("unknown", "unknown")``。

    只读、**异常不致命**：启动期不允许因为清单缺失/损坏或 git 不可用而起不来——
    探测不到就记 warning 并显式回落（站点安装形态没有 ``.git``；开发与本机生产是
    checkout，见模块 docstring）。

    ``repo_root`` 只影响 checkout 探测（缺省 = 本模块所在的部署树根）；它同时是
    默认清单路径的父目录，因此显式传 ``manifest_path`` 而想探测别处的 checkout 时
    才需要单独给。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    path = manifest_path if manifest_path is not None else root / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        checkout = _git_checkout_info(root)
        if checkout is not None:
            logger.warning(
                "release_manifest_missing_using_checkout path=%s version=%s revision=%s",
                path, checkout[0], checkout[1][:8],
            )
            return checkout
        logger.warning("release_manifest_missing path=%s", path)
        return UNKNOWN, UNKNOWN
    except (OSError, ValueError):
        # 清单**存在但读不了**（损坏/权限）：不转去读 git——那只会掩盖一次真实的
        # 安装损坏；显式回落并让日志说话。
        logger.warning("release_manifest_unreadable path=%s", path, exc_info=True)
        return UNKNOWN, UNKNOWN

    if not isinstance(data, dict):
        logger.warning("release_manifest_shape_invalid path=%s", path)
        return UNKNOWN, UNKNOWN

    product = data.get("product")
    source = data.get("source")
    version = _clean(product.get("version") if isinstance(product, dict) else None)
    commit = _clean(source.get("revision") if isinstance(source, dict) else None)
    return version or UNKNOWN, commit or UNKNOWN
