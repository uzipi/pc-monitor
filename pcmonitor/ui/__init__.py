"""终端界面。"""

from .dashboard import DEFAULT_MODE, MODE_DETAIL, MODE_OVERVIEW, Dashboard
from .theme import Theme, terminal_size

__all__ = [
    "DEFAULT_MODE",
    "Dashboard",
    "MODE_DETAIL",
    "MODE_OVERVIEW",
    "Theme",
    "terminal_size",
]
