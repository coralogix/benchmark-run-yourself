import asyncio
import hashlib
import hmac
import json

from aiohttp.test_utils import TestClient, TestServer

from harness.sims.common import LatencySampler
from harness.sims.partner_sim import PartnerState, verify_signature
from harness.sims.partner_sim import build_apps as partner_apps
from harness.sims.psp_sim import PspState
from harness.sims.psp_sim import build_apps as psp_apps


async def _nosleep(_s):
    return None


def run(coro):
    return asyncio.run(coro)


def test_latency_sampler_deterministic_and_fitted():
    a = [LatencySampler(1).sample_ms(1500, 6000) for _ in range(1)]
    s1, s2 = LatencySampler(7), LatencySampler(7)
    xs = [s1.sample_ms(1500, 6000) for _ in range(20000)]
    assert xs == [s2.sample_ms(1500, 6000) for _ in range(20000)]
    xs.sort()
    assert 1350 < xs[10000] < 1650
    assert 5000 < xs[19800] < 7200
    assert a


def test_psp_idempotency_and_duplicates():
    async def go():
        st = PspState(1, sleep=_nosleep)
        api, _ = psp_apps(st)
        async with TestClient(TestServer(api)) as c:
            body = {"amount_pence": 1234, "currency": "GBP", "order_ref": "LD-1", "token": "tok_ok"}
            r1 = await c.post("/v1/charges", json=body, headers={"Idempotency-Key": "k1"})
            r2 = await c.post("/v1/charges", json=body, headers={"Idempotency-Key": "k1"})
            assert r1.status == r2.status == 201
            j1, j2 = await r1.json(), await r2.json()
            assert j1["charge_id"] == j2["charge_id"]
            # key reuse with different params is rejected
            r = await c.post("/v1/charges", json={**body, "amount_pence": 1}, headers={"Idempotency-Key": "k1"})
            assert r.status == 422
            # no key: a second capture for the same order (duplicate)
            r3 = await c.post("/v1/charges", json=body)
            assert r3.status == 201
            r4 = await c.post("/v1/charges", json={**body, "token": "tok_decline"})
            assert r4.status == 402
            # refunds: full refund ok, then over-refund 409, unknown 404
            rf = await c.post("/v1/refunds", json={"charge_id": j1["charge_id"], "amount_pence": 1234})
            assert rf.status == 201
            rf2 = await c.post("/v1/refunds", json={"charge_id": j1["charge_id"], "amount_pence": 1})
            assert rf2.status == 409
            rf3 = await c.post("/v1/refunds", json={"charge_id": "ch_nope"})
            assert rf3.status == 404
        s = st.stats()
        assert s["charges_total"] == 2
        assert s["captured_pence_total"] == 2468
        assert s["orders_with_duplicate_captures"] == 1
        assert s["duplicate_capture_count"] == 1
        assert s["idempotent_replays"] == 1
        assert s["refunds_total"] == 1
        assert s["over_refunds"] == 1

    run(go())


def test_psp_concurrent_same_key_single_capture():
    async def go():
        async def slow(_s):
            await asyncio.sleep(0.05)

        st = PspState(1, sleep=slow)
        body = {"amount_pence": 500, "order_ref": "LD-2", "token": "tok_ok"}
        res = await asyncio.gather(*(st.charge(dict(body), "same") for _ in range(5)))
        assert {r[1]["charge_id"] for r in res} == {"ch_00000001"}
        assert st.stats()["charges_total"] == 1

    run(go())


def test_psp_admin_requires_token(monkeypatch):
    monkeypatch.setenv("LARDER_ADMIN_TOKEN", "t0k")

    async def go():
        st = PspState(1, sleep=_nosleep)
        _, admin = psp_apps(st)
        async with TestClient(TestServer(admin)) as c:
            assert (await c.get("/admin/stats")).status == 403
            assert (await c.get("/admin/stats", headers={"X-Admin-Token": "bad"})).status == 403
            r = await c.post("/admin/mode", json={"error_rate": 1.0}, headers={"X-Admin-Token": "t0k"})
            assert r.status == 200 and (await r.json())["error_rate"] == 1.0

    run(go())


def test_partner_signature_check():
    secret = "whsec_larder_partner_2019"
    raw = json.dumps({"event": "order.confirmed", "order_ref": "LD-1", "schema_version": 2}).encode()
    good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert verify_signature(raw, good, secret) == "valid"
    assert verify_signature(raw + b" ", good, secret) == "invalid"
    assert verify_signature(raw, "sha256=deadbeef", secret) == "invalid"
    assert verify_signature(raw, None, secret) == "unsigned"

    async def go():
        st = PartnerState(1, secret, sleep=_nosleep)
        api, _ = partner_apps(st)
        async with TestClient(TestServer(api)) as c:
            for hdr in ({"X-Larder-Signature": good}, {"X-Larder-Signature": good}, {}, {"X-Larder-Signature": "sha256=00"}):
                r = await c.post("/hooks", data=raw, headers={"Content-Type": "application/json", **hdr})
                assert r.status == 200
            assert (await c.post("/hooks", json={"no": "event"})).status == 400
        s = st.stats()
        assert s["signed_valid"] == 2 and s["signed_invalid"] == 1 and s["unsigned"] == 1
        assert s["duplicate_deliveries"] == 3
        assert s["deliveries_by_event"] == {"order.confirmed": {"2": 4}}

    run(go())


def test_partner_failure_injection():
    async def go():
        st = PartnerState(3, "s", sleep=_nosleep)
        st.mode["failure_rate"] = 1.0
        status, _ = await st.receive(b'{"event":"order.confirmed","order_ref":"x"}', None)
        assert status == 500
        assert st.stats()["failures_injected"] == 1 and st.stats()["delivered_total"] == 0

    run(go())


def test_email_sim_accepts_and_records(monkeypatch):
    from harness.sims.email_sim import EmailState
    from harness.sims.email_sim import build_apps as email_apps

    monkeypatch.setenv("LARDER_ADMIN_TOKEN", "t0k")
    H = {"X-Admin-Token": "t0k"}

    async def go():
        st = EmailState(1, sleep=_nosleep)
        api, admin = email_apps(st)
        async with TestClient(TestServer(api)) as c, TestClient(TestServer(admin)) as a:
            msg = {"to": "ann@example.com", "template": "order_confirmed",
                   "data": {"order_ref": "LD-1", "items": [{"sku": "SKU-1", "qty": 1}], "slot": {"window": "am"}}}
            r = await c.post("/v1/messages", json=msg)
            assert r.status == 202 and (await r.json())["message_id"] == "msg_00000001"
            r = await c.post("/v1/messages", json={"to": "bob@example.com", "template": "order_dispatched", "data": {"order_ref": "LD-1"}})
            assert r.status == 202
            assert (await c.post("/v1/messages", json={"to": "bob@example.com", "template": "order_dispatched",
                                                      "data": {"order_ref": "LD-1"}})).status == 202
            assert (await c.post("/v1/messages", json={"to": "nope", "template": "x"})).status == 400
            assert (await c.post("/v1/messages", json={"to": "a@b.c"})).status == 400
            assert (await a.get("/admin/stats")).status == 403
            s = await (await a.get("/admin/stats", headers=H)).json()
            assert s["messages_total"] == 3
            assert s["messages_by_template"] == {"order_confirmed": 1, "order_dispatched": 2}
            assert s["messages_with_items_by_template"] == {"order_confirmed": 1}
            assert s["messages_with_slot_by_template"] == {"order_confirmed": 1}
            assert s["duplicate_messages"] == 1 and s["rejected"] == 2 and s["unique_recipients"] == 2
            m = await (await a.get("/admin/messages?to=ann@example.com", headers=H)).json()
            assert m["total"] == 1 and m["messages"][0]["data"]["order_ref"] == "LD-1"
            r = await a.post("/admin/mode", json={"latency_p50_ms": 2500, "latency_p99_ms": 8000, "failure_rate": 1}, headers=H)
            assert (await r.json())["latency_p50_ms"] == 2500
            r = await c.post("/v1/messages", json=msg)
            assert r.status == 503 and (await r.json())["error"] == "provider_unavailable"
            s = await (await a.get("/admin/stats", headers=H)).json()
            assert s["failures_injected"] == 1 and s["failures_by_template"] == {"order_confirmed": 1}
            r = await a.post("/admin/mode", json={"preset": "normal"}, headers=H)
            assert (await r.json())["failure_rate"] == 0.0
            await a.post("/admin/reset", headers=H)
            s = await (await a.get("/admin/stats", headers=H)).json()
            assert s["messages_total"] == 0

    run(go())


def test_email_sim_latency_deterministic():
    from harness.sims.email_sim import EmailState

    async def go(seed):
        slept = []

        async def rec(s):
            slept.append(s)

        st = EmailState(seed, sleep=rec)
        st.mode.update({"latency_p50_ms": 2500, "latency_p99_ms": 8000, "failure_rate": 0.1})
        codes = [(await st.send({"to": "a@b.c", "template": "t"}))[0] for _ in range(300)]
        return slept, codes

    a, b = run(go(5)), run(go(5))
    assert a == b
    slept = sorted(a[0])
    assert 2.0 < slept[150] < 3.0
    assert 0 < a[1].count(503) < 60
