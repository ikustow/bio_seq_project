"""Session-aware LangChain agent with LangGraph persistence hooks."""

__all__ = ["SessionGraphAgent"]


def __getattr__(name: str):
    if name == "SessionGraphAgent":
        from .agent import SessionGraphAgent

        return SessionGraphAgent
    raise AttributeError(name)
