"""accounts models (see larder_common/dbx/model.py)."""
import datetime

from larder_common.dbx.model import Field, Model


def _now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class User(Model):
    __table__ = "users"
    __database__ = "accounts"
    user_id = Field(str, pk=True)
    name = Field(str)
    email = Field(str)
    postcode = Field(str)
    created = Field(str)

    def to_api(self):
        return {
            "user_id": self.user_id,
            "name": self.name,
            "email": self.email,
            "postcode": self.postcode,
            "created": self.created,
        }


class Address(Model):
    __table__ = "addresses"
    __database__ = "accounts"
    id = Field(int, pk=True)
    user_id = Field(str)
    label = Field(str)
    line1 = Field(str)
    line2 = Field(str)
    town = Field(str)
    postcode = Field(str)
    is_default = Field(int, default=0)
    created_at = Field(str, default=_now)


class Preferences(Model):
    __table__ = "preferences"
    __database__ = "accounts"
    user_id = Field(str, pk=True)
    email_opt_in = Field(bool, default=False)
    sms_opt_in = Field(bool, default=False)
    updated_at = Field(str, default=_now)
