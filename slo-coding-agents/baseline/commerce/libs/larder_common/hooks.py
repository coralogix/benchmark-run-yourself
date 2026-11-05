"""
Tiny synchronous event-hook registry (was larder/hooks.py).

    from larder_common.hooks import HookRegistry
    HOOKS = HookRegistry(("order_confirmed", "order_failed"))

    @HOOKS.register("order_confirmed")
    def my_hook(**ctx): ...

    HOOKS.run("order_confirmed", order=..., cart=...)

Hooks run synchronously, in registration order, in the calling thread.
Exceptions propagate to the caller (on purpose: "fail loudly", RB 2018).
"""
import importlib

from larder_common.log import get_logger

log = get_logger("hooks")


class HookRegistry(object):
    def __init__(self, events):
        self.events = tuple(events)
        self._registry = {e: [] for e in self.events}
        self._loaded = False

    def register(self, event, fn=None):
        if event not in self._registry:
            raise ValueError("unknown hook event %r" % event)

        def deco(f):
            if f not in self._registry[event]:
                self._registry[event].append(f)
            return f

        if fn is not None:
            return deco(fn)
        return deco

    def unregister(self, event, fn):
        if fn in self._registry.get(event, []):
            self._registry[event].remove(fn)

    def hooks_for(self, event):
        return list(self._registry.get(event, []))

    def run(self, event, **ctx):
        for fn in self._registry.get(event, []):
            log.debug("running hook %s.%s for %s", fn.__module__, fn.__name__, event)
            fn(**ctx)

    def load_dotted(self, event, paths):
        """Register hooks given as 'pkg.module.func' strings. Once only."""
        if self._loaded:
            return
        for path in paths:
            mod_name, fn_name = path.rsplit(".", 1)
            mod = importlib.import_module(mod_name)
            self.register(event, getattr(mod, fn_name))
        self._loaded = True

    def reset(self):
        self._registry = {e: [] for e in self.events}
        self._loaded = False
