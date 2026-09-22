"""Independent provider; never replaces the built-in parallel registration."""
from .provider import KomatsoParallelProvider


def register(ctx):
    ctx.register_web_search_provider(KomatsoParallelProvider())
