"""Compatibility loader for Rich.

Imports the standalone `rich` package if installed, or transparently falls back
to `pip._vendor.rich` so `attendpy` runs out-of-the-box in any standard Python
environment that includes `pip`.

Also customizes all Prompt classes to use ' -> ' instead of ': ' as the prompt suffix.
"""

from __future__ import annotations

import sys

try:
    import rich  # noqa: F401
    from rich import box
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.prompt import (
        Confirm as _Confirm,
        FloatPrompt as _FloatPrompt,
        IntPrompt as _IntPrompt,
        Prompt as _Prompt,
        PromptBase,
    )
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover
    from pip._vendor import rich  # type: ignore # noqa: F401
    from pip._vendor.rich import box  # type: ignore
    from pip._vendor.rich.align import Align  # type: ignore
    from pip._vendor.rich.columns import Columns  # type: ignore
    from pip._vendor.rich.console import Console, Group  # type: ignore
    from pip._vendor.rich.live import Live  # type: ignore
    from pip._vendor.rich.panel import Panel  # type: ignore
    from pip._vendor.rich.prompt import (  # type: ignore
        Confirm as _Confirm,
        FloatPrompt as _FloatPrompt,
        IntPrompt as _IntPrompt,
        Prompt as _Prompt,
        PromptBase,
    )
    from pip._vendor.rich.rule import Rule  # type: ignore
    from pip._vendor.rich.table import Table  # type: ignore
    from pip._vendor.rich.text import Text  # type: ignore

# Set global default prompt suffix to " -> " across all Rich prompt classes
PromptBase.prompt_suffix = " -> "


class Prompt(_Prompt):
    prompt_suffix = " -> "


class Confirm(_Confirm):
    prompt_suffix = " -> "


class FloatPrompt(_FloatPrompt):
    prompt_suffix = " -> "


class IntPrompt(_IntPrompt):
    prompt_suffix = " -> "


console = Console()


def clear_terminal_screen() -> None:
    """Clear both the visible terminal screen and the scrollback buffer if in a TTY."""
    if sys.stdout.isatty():
        sys.stdout.write("\x1b[2J\x1b[3J\x1b[H")
        sys.stdout.flush()


__all__ = [
    "Align",
    "Columns",
    "Confirm",
    "Console",
    "FloatPrompt",
    "Group",
    "IntPrompt",
    "Live",
    "Panel",
    "Prompt",
    "Rule",
    "Table",
    "Text",
    "box",
    "clear_terminal_screen",
    "console",
]
