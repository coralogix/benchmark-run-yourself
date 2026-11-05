"""
Post-checkout hook registry.

    from services.checkout import hooks

    @hooks.register("order_confirmed")
    def my_hook(checkout, cart, breakdown, body):
        ...

Hooks run synchronously, in registration order, inside the checkout request
once ops has confirmed the order. Keep them fast. The default hooks are
listed in settings.CHECKOUT_HOOKS and loaded by load_default_hooks().

If a hook raises, the exception propagates and the customer gets a 500 -
even though the order is confirmed and paid. (RB 2018: "fail loudly, then we
notice". We have noticed. TODO(TH 2023-07-19): make hooks best-effort.)
"""
from larder_common.hooks import HookRegistry

EVENTS = ("order_confirmed", "order_failed")

# module-level registry, shared by every request thread
REGISTRY = HookRegistry(EVENTS)

register = REGISTRY.register
unregister = REGISTRY.unregister
hooks_for = REGISTRY.hooks_for
run = REGISTRY.run


def load_default_hooks():
    from larder_common import settings

    REGISTRY.load_dotted("order_confirmed", settings.CHECKOUT_HOOKS)
