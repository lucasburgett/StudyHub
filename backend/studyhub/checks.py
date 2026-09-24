"""Connection checks shared by `studyhub check` and the settings page's Test buttons."""

from __future__ import annotations

from .config import SOURCES, Settings, get_settings

TARGETS = (*SOURCES, "claude", "search")


def describe_error(e: Exception) -> str:
    """A short, useful message: which host couldn't be reached, or what it answered."""
    import httpx

    if isinstance(e, httpx.HTTPStatusError):
        return f"{e.request.url.host} answered {e.response.status_code} {e.response.reason_phrase}."
    if isinstance(e, httpx.TransportError):
        try:
            host = e.request.url.host
        except RuntimeError:
            host = "the server"
        return f"Couldn't reach {host} ({e.__class__.__name__}: {e}). Check your network connection."
    return str(e) or e.__class__.__name__


def check(target: str, settings: Settings | None = None) -> tuple[bool, str]:
    """(ok, message) for one source, the Claude key, or semantic search."""
    settings = settings or get_settings()
    if target in SOURCES:
        if not settings.configured(target):
            return False, "Not set up yet."
        from .connectors import get_connector

        try:
            return True, get_connector(target).check(settings)
        except Exception as e:
            return False, describe_error(e)
    if target == "claude":
        backend = settings.agent_backend
        if backend is None:
            return False, ("Add an API key, or log in to Claude Code (“claude auth login” in a terminal) "
                           "to use your Claude subscription.")
        if backend == "subscription":
            from .agent.subscription import check_login

            return check_login(settings)
        import anthropic

        from .agent.chat import make_client

        try:
            model = make_client().models.retrieve(settings.model)
            return True, f"Key works. Chat uses {model.display_name or settings.model}."
        except anthropic.AuthenticationError:
            return False, "Anthropic rejected this key."
        except anthropic.APIError as e:
            return False, getattr(e, "message", None) or str(e)
    if target == "search":
        from .embeddings import get_embedder

        embedder = get_embedder(settings)
        if embedder is None:
            return False, "Off: search is keyword-only. Add a Voyage key or install local embeddings."
        try:
            embedder.query("test")
            return True, f"Working ({embedder.name})."
        except Exception as e:
            return False, describe_error(e)
    raise ValueError(f"unknown check: {target}")
