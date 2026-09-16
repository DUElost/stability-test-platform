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

**裁决（见 Agent Note）**：
- 真值来源优先级 = 清单 > 显式 ``unknown``；**不**回落 ``backend.__version__``
  （那是第二处会漂移的字面量，已删除）；
- 不带 ``database.schema_target`` label：它是**安装期声明值**，与 ``/health`` 的
  ``alembic_head``（运行期实测）语义不同，加 label 等于造第三个事实源。
"""
from __future__ import annotations

import json
import logging
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


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def resolve_build_info(manifest_path: Optional[Path] = None) -> Tuple[str, str]:
    """返回 ``(version, commit)``；读不到清单时回落 ``("unknown", "unknown")``。

    只读、**异常不致命**：启动期不允许因为一份清单缺失/损坏而起不来，缺失时记
    warning 并显式回落（开发态 = git checkout，本就没有清单）。
    """
    path = manifest_path if manifest_path is not None else _REPO_ROOT / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("release_manifest_missing path=%s", path)
        return UNKNOWN, UNKNOWN
    except (OSError, ValueError):
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
