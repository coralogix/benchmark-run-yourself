"""API error types. Raise these from views/services; web.py maps them to JSON."""


class LarderError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, code=None, status=None, **extra):
        super().__init__(code or self.code)
        if code:
            self.code = code
        if status:
            self.status = status
        self.extra = extra

    def to_dict(self):
        d = {"error": self.code}
        d.update(self.extra)
        return d


class NotFound(LarderError):
    status = 404
    code = "not_found"


class BadRequest(LarderError):
    status = 400
    code = "bad_request"


class CouponError(LarderError):
    status = 400
    code = "invalid_coupon"


class Conflict(LarderError):
    status = 409
    code = "conflict"


class UpstreamError(LarderError):
    status = 502
    code = "upstream_error"


class PassThrough(LarderError):
    """Re-raise an upstream service's error response as-is (status + body).

    Used all over edge-api: whatever the backend said, the customer gets.
    """

    def __init__(self, status, body):
        body = dict(body or {})
        code = body.pop("error", None) or "upstream_error"
        super().__init__(code, status=status, **body)


# old name, still imported by legacy/v1_routes.py
ApiError = LarderError
