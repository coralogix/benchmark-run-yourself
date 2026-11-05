"""
CRM push (2018-2021). Disabled via settings.CRM_ENABLED (config/defaults.ini [crm], larder_common/settings.py).

The CRM vendor contract ended in 2021. Left wired up because the
"re-enable CRM" ticket keeps getting reprioritised.
"""
import requests

from larder_common import settings
from larder_common.log import get_logger

log = get_logger("legacy.crm")

TIMEOUT = 10


def push_order(order):
    payload = {
        "type": "order",
        "ref": order.order_ref,
        "customer": order.user_id,
        "value": order.total,
        "currency": "GBP",
    }
    resp = requests.post(settings.CRM_ENDPOINT, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    log.info("pushed order %s to crm", order.order_ref)


def push_customer(user):
    payload = {"type": "customer", "id": user.user_id, "email": user.email, "postcode": user.postcode}
    resp = requests.post(settings.CRM_ENDPOINT, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
