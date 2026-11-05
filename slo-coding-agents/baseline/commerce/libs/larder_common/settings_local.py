# settings_local.py - per-box overrides for the production web nodes.
#
# Originally copied onto lon-web-01/02 by the ansible role; checked in when
# we moved off the VMs so the container gets the same behaviour. Moved from
# larder/ to libs/larder_common/ in the 2023 split, so it now applies to
# EVERY commerce service.
# Anything UPPERCASE here is applied on top of larder_common/settings.py.

# KP 2020-02-11: v2 pricing rollout paused after the FAMILY12 complaints,
# keep prod on the old engine until finance signs off. See OPS-1182.
PRICING_ENGINE = "v1"

# DP 2019-08-30: smaller result pages on prod, the old mobile app chokes
SEARCH_RESULTS_LIMIT = 40

# TODO(KP 2020-02-11): remove once v2 is live
LEGACY_COUPON_ROUNDING = True

LOG_LEVEL = "INFO"

# SB 2022-09-14: two retries is what ops asked for after INC-388
HTTP_CLIENT_EXTRA_ATTEMPTS = 2

# INC-2187: per-service memory ceilings, matched to the container limits in
# prod. Over the limit the service exits cleanly and is restarted (serve.py).
MEMORY_LIMITS_MB = {"cart": 160}
