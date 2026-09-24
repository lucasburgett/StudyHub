"""Source connectors. Each copies one upstream service into the local store."""

from __future__ import annotations

from .base import Connector, SyncContext


def get_connector(source: str) -> Connector:
    if source == "canvas":
        from .canvas import CanvasConnector
        return CanvasConnector()
    if source == "gradescope":
        from .gradescope import GradescopeConnector
        return GradescopeConnector()
    if source == "goodnotes":
        from .goodnotes import GoodNotesConnector
        return GoodNotesConnector()
    if source == "granola":
        from .granola import GranolaConnector
        return GranolaConnector()
    raise ValueError(f"unknown source: {source}")


__all__ = ["Connector", "SyncContext", "get_connector"]
