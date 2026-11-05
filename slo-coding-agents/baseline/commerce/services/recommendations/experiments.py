"""
A/B bucketing for recommendations (SB 2023-10, PM-1004). Dead.

The experiment ("show 4 vs 6 also-bought products") was planned for Q4 2023
and then descoped. bucket() is deterministic per user so the two arms would
have been stable. routes.py never passes a user id, so nothing calls this.
"""
import hashlib

EXPERIMENT = "recs-count-2023q4"
ARMS = {"control": 6, "fewer": 4}
# percent of traffic in the "fewer" arm
FEWER_PERCENT = 50


def bucket(user_id, experiment=EXPERIMENT):
    if not user_id:
        return "control"
    h = int(hashlib.md5(("%s:%s" % (experiment, user_id)).encode()).hexdigest()[:8], 16)
    return "fewer" if (h % 100) < FEWER_PERCENT else "control"


def limit_for(user_id):
    return ARMS[bucket(user_id)]
