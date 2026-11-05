"""checkout models (see larder_common/dbx/model.py)."""
import datetime

from larder_common.dbx.model import Field, Model


def _now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class Checkout(Model):
    __table__ = "checkouts"
    __database__ = "checkout"
    order_ref = Field(str, pk=True)
    cart_id = Field(str)
    user_id = Field(str)
    status = Field(str, default="pending")
    subtotal = Field(float)
    discount = Field(float)
    delivery_fee = Field(float)
    total = Field(float)
    total_pence = Field(int)
    coupon_code = Field(str)
    hold_id = Field(str)
    charge_id = Field(str)
    engine = Field(str)
    error = Field(str)
    attempts = Field(int, default=0)
    created_at = Field(str, default=_now)
    updated_at = Field(str, default=_now)

    def touch(self):
        self.updated_at = _now()
        return self.save()

    def to_api(self):
        return {
            "order_ref": self.order_ref,
            "cart_id": self.cart_id,
            "user_id": self.user_id,
            "status": self.status,
            "total_pence": self.total_pence,
            "total": self.total,
            "coupon": self.coupon_code,
            "hold_id": self.hold_id,
            "charge_id": self.charge_id,
            "engine": self.engine,
            "error": self.error,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CheckoutEvent(Model):
    __table__ = "checkout_events"
    __database__ = "checkout"
    id = Field(int, pk=True)
    order_ref = Field(str)
    event = Field(str)
    payload = Field(str)
    created_at = Field(str, default=_now)
