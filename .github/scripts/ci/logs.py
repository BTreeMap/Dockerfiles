"""One logging configuration, applied at the imperative boundary.

Configured on the `ci` root logger rather than per module so every component
inherits it by name and no module has to reach for another's handler.
"""

from __future__ import annotations

import logging
import sys


def configure(level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger("ci")
    if root.handlers:
        return root

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d.%H-%M-%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    return root
