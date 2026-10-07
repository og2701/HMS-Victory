"""Panels by key: each panel module registers how to draw a screen and how to run its actions.

    @view("shop")
    def shop(profile, ctx) -> dict: ...                         # an SkPanel (common.panel)

    @act("shop")
    def shop_act(profile, ctx, action, body) -> dict: ...       # an SkPanelResult (common.result)

`ctx` is {"uid": int, "name": str | None, "client": discord client}. An act raises common.Refuse for anything
the game won't do; it saves the profile itself (E.save_profile) when it changes it.
"""

PANELS: dict[str, dict] = {}


def view(key: str):
    def deco(fn):
        PANELS.setdefault(key, {})["view"] = fn
        return fn
    return deco


def act(key: str):
    def deco(fn):
        PANELS.setdefault(key, {})["act"] = fn
        return fn
    return deco


def load_all():
    """Import every panel module so it registers (they're imported for their side effect)."""
    import importlib
    import pkgutil
    import lib.activities.skyrim_web as pkg
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("panels_") or m.name in ("bouts",):
            importlib.import_module(f"{pkg.__name__}.{m.name}")
