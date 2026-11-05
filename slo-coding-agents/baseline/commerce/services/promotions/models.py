"""promotions models (see larder_common/dbx/model.py)."""
from larder_common.dbx.model import Field, Model


class Coupon(Model):
    __table__ = "coupons"
    __database__ = "promotions"
    code = Field(str, pk=True)
    percent_off = Field(float, default=0)
    min_spend = Field(float, default=0)  # pounds
    expires = Field(str)  # free text, see coupons.parse_expiry
    free_delivery = Field(bool, default=False)
    active = Field(int, default=1)

    def as_payload(self):
        return {
            "code": self.code,
            "percent_off": self.percent_off,
            "free_delivery": bool(self.free_delivery),
        }

    def as_dict(self):
        return {
            "code": self.code,
            "percent_off": self.percent_off,
            "free_delivery": bool(self.free_delivery),
            # pounds -> pence. int() truncates; 0.1 pound minimums don't exist (KP)
            "min_spend_pence": int((self.min_spend or 0) * 100),
            "expires": self.expires,
            "active": bool(self.active),
        }


class Redemption(Model):
    __table__ = "redemptions"
    __database__ = "promotions"
    id = Field(int, pk=True)
    code = Field(str)
    user_id = Field(str)
    order_ref = Field(str)
    created_at = Field(str)
