"""
Identifier helpers (TH 2023-02).

Supposed to replace utils.gen_ref / utils.gen_id. Uses a Crockford-ish
alphabet so refs are easier to read out over the phone. Nothing uses it yet:
checkout still calls utils.gen_ref, so order refs are hex. If you switch,
note ops' orders validates refs with /^LD-[0-9A-F]{10}$/ (it might; ask ops).
"""
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def readable(n=10):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def order_ref(prefix="LD-"):
    return prefix + readable(10)


def cart_id():
    return "c_" + secrets.token_hex(6)


def hold_ref(cart_id):
    # slots used to want "<cart>-<n>"; now it takes cart_ref = cart_id as-is
    return "%s-%d" % (cart_id, 1)
