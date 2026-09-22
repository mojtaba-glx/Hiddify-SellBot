"""X-NET integration (experimental, disabled by default).

No existing bot module imports this package yet. This is intentional: X-NET can
be developed and tested without changing production Hiddify flows.
"""
from .adapter import XNetAdapter

__all__ = ["XNetAdapter"]
