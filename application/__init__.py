"""Keep non-GUI release tools usable without importing Qt."""

__all__ = ["App"]


def __getattr__(name):
    if name == "App":
        from .app import App

        return App
    raise AttributeError(name)
