"""源扫描型守卫的公共锚点助手（#2639）。

**要解决的问题**：本仓有一族「读被测模块的源码文本，再断言某字面量在/不在其中」的守卫。
它的有效性完全依赖「锚点与被扫模块仍然重合」，而这件事**默认没人检查**。于是有三种失败形态，
其中第三种最隐蔽（#2639 实测，24 小时内坏三例）：

1. 被扫逻辑搬走 → `import` 失败 → collection error，整模块 0 条（响亮，容易发现）；
2. 断言的字面量搬走 → 正向断言恒红（响亮）；
3. **否定断言恒真** → 守卫还在跑、还是绿的，但它守的是一份已经没有那些代码的文件
   （`assert "row.state = ev.state" not in src` 在 src 里根本没有 DLE 落库点时永远成立）。

本助手把「取源码」与「证明锚点在场」绑成一个不可拆开的动作，让 ③ 与「真实回归」可区分：

- 锚点找不到 → `AnchorDrift`（**用例已过期**，该修的是测试）；
- 锚点在、形态不对 → `FormRegression`（**防线回归**，该修的是产品代码）；
- 没先声明锚点就做断言 → `GuardMisuse`（禁止「空守」写法）。

三类都派生自 `AssertionError`，因此在 pytest 里都是 failed（不会退化成 collection error），
但消息前缀不同，看红即可判因。

**典型用法**（迁移后的 #1956/#2025 防线）::

    guard = (
        SourceGuard.of_module(agent_device_log_events)
        .anchored("resolve_initial_upload_state(ev.event_type, ev.state)")
    )
    guard.assert_count("state=resolve_initial_upload_state(ev.event_type, ev.state)", 2)
    guard.assert_absent("row.state = ev.state", why="#2025 裸赋值不得回潮")

注意 `anchored()` 只保证「被扫逻辑仍在这个文件里」，它**不是**被测行为；被测行为仍由
`assert_*` 表达。反过来，`assert_absent` 必须排在 `anchored()` 之后——这正是本模块唯一的强制点。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Union

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 红侧消息前缀——「用例过期」与「防线回归」必须在**第一行**就能区分。
ANCHOR_DRIFT_PREFIX = "用例已过期（锚点漂移）"
FORM_REGRESSION_PREFIX = "防线回归（真实形态退化）"
MISUSE_PREFIX = "守卫写法不合法（空守）"

ModuleLike = Union[object, str, Path]


class AnchorDrift(AssertionError):
    """锚点不在被扫对象里：测试该修（代码搬走了 / 路径失效了）。"""


class FormRegression(AssertionError):
    """锚点在位但形态不符：产品代码该修（防线抓到了真东西）。"""


class GuardMisuse(AssertionError):
    """未先 `anchored()` 就做形态断言：这种断言可以恒真，等于没守。"""


class SourceGuard:
    """按「锚点 → 形态」两步读源码文本的守卫。"""

    def __init__(self, text: str, *, origin: str) -> None:
        self._text = text
        self._origin = origin
        self._anchors: list[str] = []

    # ── 取源码（失败也要可区分）──

    @classmethod
    def of_repo_path(cls, rel: Union[str, Path]) -> "SourceGuard":
        """按**仓库根相对路径**取源码；文件不存在按锚点漂移处理。

        「路径写错/目录改名」和「代码搬走」是同一种失真，都必须报「用例已过期」——
        让它们退化成 `FileNotFoundError` 或恒真断言，就等于回到 #2639 的病。
        """
        path = REPO_ROOT / rel
        if not path.is_file():
            raise AnchorDrift(
                f"{ANCHOR_DRIFT_PREFIX}：被扫文件不存在 —— {rel}\n"
                "文件被改名/移走/删掉时，读它的用例已经不再覆盖任何东西。"
            )
        return cls(path.read_text(encoding="utf-8"), origin=str(rel))

    @classmethod
    def of_module(cls, module: object) -> "SourceGuard":
        """按模块对象取源码（替代 `Path(mod.__file__).read_text()`）。

        用 `inspect.getfile` 而不是 `__file__`：命名空间包/冻结加载器下 `__file__`
        可能不存在，而 `getfile` 的失败更明确。
        """
        try:
            path = Path(inspect.getfile(module)).resolve()  # type: ignore[arg-type]
        except (TypeError, OSError) as exc:
            raise AnchorDrift(
                f"{ANCHOR_DRIFT_PREFIX}：取不到 {getattr(module, '__name__', module)!r} 的源文件：{exc}"
            ) from exc
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise GuardMisuse(
                f"{MISUSE_PREFIX}：{path} 不在本仓库内（{REPO_ROOT}）——"
                "守卫第三方/站点外代码请用 of_repo_path 显式给出可读来源"
            ) from exc
        return cls.of_repo_path(rel)

    # ── 第一步：证明锚点在场 ──

    def anchored(self, needle: str, *, expect: int | None = None) -> "SourceGuard":
        """声明「被扫逻辑仍在此处」的正锚点；可链式多次声明。

        `expect` 用来钉命中次数：次数变了也是漂移（逻辑被复制/部分搬走时，
        只判「≥1」会放过后一种）。
        """
        found = self._text.count(needle)
        if found == 0:
            raise AnchorDrift(
                f"{ANCHOR_DRIFT_PREFIX}：{self._origin} 里找不到锚点 {needle!r}（命中 0 次）\n"
                "该逻辑很可能已随重构搬到别的模块——此后的形态断言会变成**恒真/恒假的空守**，"
                "请把本守卫改指新的真源模块（#2639 的第 3 例就是这个形态）。"
            )
        if expect is not None and found != expect:
            raise AnchorDrift(
                f"{ANCHOR_DRIFT_PREFIX}：{self._origin} 里锚点 {needle!r} 命中 {found} 次，"
                f"预期 {expect} 次——被扫逻辑被复制、删减或部分搬走。"
            )
        self._anchors.append(needle)
        return self

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(self._anchors)

    @property
    def text(self) -> str:
        return self._text

    @property
    def origin(self) -> str:
        return self._origin

    # ── 第二步：形态断言（必须先有锚点）──

    def _require_anchor(self, what: str) -> None:
        if not self._anchors:
            raise GuardMisuse(
                f"{MISUSE_PREFIX}：{self._origin} 未声明锚点就执行 {what}\n"
                "先调用 `.anchored(该模块确实还承载这段逻辑的字面量)` 再断言：否则「不在里面」"
                "可能只是因为代码搬走了（#2639 第 3 例：两条否定断言恒真）。"
            )

    def assert_absent(self, needle: str, *, why: str) -> None:
        """否定断言：锚点在、而 `needle` 出现 = 真实回归。`why` 必填（写清防的是谁）。"""
        self._require_anchor(f"assert_absent({needle!r})")
        if needle in self._text:
            raise FormRegression(
                f"{FORM_REGRESSION_PREFIX}：{self._origin} 出现了不该出现的 {needle!r}\n"
                f"防线：{why}\n"
                f"锚点 {self._anchors!r} 仍在位，所以这不是用例过期。"
            )

    def assert_present(self, needle: str, *, why: str = "") -> None:
        self._require_anchor(f"assert_present({needle!r})")
        if needle not in self._text:
            raise FormRegression(
                f"{FORM_REGRESSION_PREFIX}：{self._origin} 缺少应有的 {needle!r}"
                + (f"\n防线：{why}" if why else "")
                + f"\n锚点 {self._anchors!r} 仍在位，所以这不是用例过期。"
            )

    def assert_count(self, needle: str, expect: int, *, why: str = "") -> None:
        self._require_anchor(f"assert_count({needle!r})")
        found = self._text.count(needle)
        if found != expect:
            raise FormRegression(
                f"{FORM_REGRESSION_PREFIX}：{self._origin} 里 {needle!r} 命中 {found} 次，"
                f"预期 {expect} 次" + (f"\n防线：{why}" if why else "")
                + f"\n锚点 {self._anchors!r} 仍在位，所以这不是用例过期。"
            )
