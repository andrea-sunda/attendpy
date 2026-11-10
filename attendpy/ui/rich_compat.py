"""Compatibility loader for Rich.

Imports the standalone `rich` package if installed, or transparently falls back
to `pip._vendor.rich` so `attendpy` runs out-of-the-box in any standard Python
environment that includes `pip`.
"""

from __future__ import annotations

try:
    import rich  # noqa: F401
    from rich import box
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover
    from pip._vendor import rich  # type: ignore # noqa: F401
    from pip._vendor.rich import box  # type: ignore
    from pip._vendor.rich.align import Align  # type: ignore
    from pip._vendor.rich.columns import Columns  # type: ignore
    from pip._vendor.rich.console import Console, Group  # type: ignore
    from pip._vendor.rich.panel import Panel  # type: ignore
    from pip._vendor.rich.prompt import (  # type: ignore
        Confirm,
        FloatPrompt,
        IntPrompt,
        Prompt,
    )
    from pip._vendor.rich.rule import Rule  # type: ignore
    from pip._vendor.rich.table import Table  # type: ignore
    from pip._vendor.rich.text import Text  # type: ignore

console = Console()

__all__ = [
    "Align",
    "Columns",
    "Confirm",
    "Console",
    "FloatPrompt",
    "Group",
    "IntPrompt",
    "Panel",
    "Prompt",
    "Rule",
    "Table",
    "Text",
    "box",
    "console",
]
