"""The 12 datasets: one DataPrime query each, run as a Background Query over the replayed week.

DATASETS maps each dataset to its query, the timestamp field it is partitioned on, whether it is filled in daily chunks
(Background Queries return at most 1 million rows each) or in one week-long query, and its description.
"""
A="$d.attributes"; L="$d.logRecord.attributes"
def a(p,k,alias,num=False): return f"{p}['{k}']{':number' if num else ''} as {alias}"
GAMING = f"""source logs
| filter $l.applicationname == 'goldspin-play' && {A}['event.domain'] == 'business'
| choose {a(A,'event.name','event')}, {A}['outcome'] as outcome, $l.subsystemname as service, {a(A,'user.id','user_id')}, {a(A,'user.segment','user_segment')}, {a(A,'session.id','session_id')}, {a(A,'money.amount_minor','amount_minor',True)}, {a(A,'money.currency','currency')}, {a(A,'money.direction','direction')}, {a(A,'money.kind','money_kind')}, {a(A,'money.margin_minor','margin_minor',True)}, {a(A,'payment.method','payment_method')}, {a(A,'decline.code','decline_code')}, {a(A,'decline.issuer','decline_issuer')}, {a(A,'decline.reason','decline_reason')}, {a(A,'reject.reason','reject_reason')}, {a(A,'hold.reason','hold_reason')}, {a(A,'block.reason','block_reason')}, {a(A,'bet.market','market')}, {a(A,'bet.selection','selection')}, {a(A,'bet.selections','selections',True)}, {a(A,'bet.price','price',True)}, {a(A,'jackpot.game','jackpot_game')}, {a(A,'jackpot.tier','jackpot_tier')}"""
STORE = f"""source logs
| filter $l.applicationname == 'coralogix-store' && {L}['event.domain'] == 'business'
| choose {a(L,'event.name','event')}, {L}['outcome'] as outcome, $l.subsystemname as service, {a(L,'user.id','user_id')}, {a(L,'user.segment','user_segment')}, {a(L,'customer.type','customer_type')}, {a(L,'session.id','session_id')}, {a(L,'money.amount_minor','amount_minor',True)}, {a(L,'money.currency','currency')}, {a(L,'money.direction','direction')}, {a(L,'money.kind','money_kind')}, {a(L,'order.items','order_items',True)}, {a(L,'order.lines','order_lines',True)}, {a(L,'order.discount_minor','order_discount_minor',True)}, {a(L,'order.margin_minor','order_margin_minor',True)}, {a(L,'order.promo_code','promo_code')}, {a(L,'delivery.method','delivery_method')}, {a(L,'payment.method','payment_method')}, {a(L,'product.sku','sku')}, {a(L,'product.name','product_name')}, {a(L,'product.department','department')}, {a(L,'line.qty','line_qty',True)}, {a(L,'line.unit_price_minor','line_unit_price_minor',True)}, {a(L,'line.full_price_minor','line_full_price_minor',True)}, {a(L,'line.discount_minor','line_discount_minor',True)}, {a(L,'line.revenue_minor','line_revenue_minor',True)}, {a(L,'line.cost_minor','line_cost_minor',True)}, {a(L,'line.margin_minor','line_margin_minor',True)}, {a(L,'line.value_minor','line_value_minor',True)}, {a(L,'basket.items','basket_items',True)}, {a(L,'abandon.stage','abandon_stage')}, {a(L,'stock.cause','stock_cause')}, {a(L,'decline.code','decline_code')}, {a(L,'decline.reason','decline_reason')}"""
FIN = f"""source logs
| filter $l.applicationname == 'coralogix-finance' && {L}['event.domain'] == 'business'
| choose {a(L,'event.name','event')}, {L}['outcome'] as outcome, $l.subsystemname as service, {a(L,'user.id','user_id')}, {a(L,'user.segment','client_segment')}, {a(L,'session.id','session_id')}, {a(L,'money.amount_minor','amount_minor',True)}, {a(L,'money.currency','currency')}, {a(L,'money.direction','direction')}, {a(L,'cost.kind','cost_kind')}, {a(L,'research.asset_class','asset_class')}, {a(L,'research.region','region')}, {a(L,'research.cited','cited')}, {a(L,'research.citation.count','citation_count',True)}, {a(L,'research.retrieval_passes','retrieval_passes',True)}, {a(L,'vendor.calls','vendor_calls',True)}, {a(L,'guardrails.blocked','guardrail_blocked')}, {a(L,'refund.reason','refund_reason')}, {a(L,'export.format','export_format')}"""
ERR = """source logs
| filter ($l.applicationname == 'goldspin-play' || $l.applicationname == 'coralogix-store' || $l.applicationname == 'coralogix-finance') && $m.severity >= WARNING
| create err_type from firstNonNull($d.attributes['error.type'], $d.logRecord.attributes['error.type'], 'none')
| groupby roundTime($m.timestamp, 1h) as hour, $l.applicationname as app, $l.subsystemname as service, $m.severity as severity, err_type as error_type aggregate count() as events"""


def _amt(p): return f"{p}['money.amount_minor']:number"
PLAYER_DAILY = f"""source logs
| filter $l.applicationname == 'goldspin-play' && {A}['event.domain'] == 'business' && {A}['user.id'] != null
| create ev from {A}['event.name']:string
| create amt from {_amt(A)}
| create mg from {A}['money.margin_minor']:number
| groupby roundTime($m.timestamp, 1d) as day, {A}['user.id']:string as user_id, {A}['user.segment']:string as user_segment aggregate
    count() as events,
    sum(if(ev == 'bet.accepted', 1, 0)) as bets_accepted,
    sum(if(ev == 'bet.rejected', 1, 0)) as bets_rejected,
    sum(if(ev == 'bet.accepted', amt, 0)) as stake_minor,
    sum(if(ev == 'bet.won', amt, 0)) as winnings_paid_minor,
    sum(if(ev == 'bet.won' || ev == 'bet.lost', mg, 0)) as house_margin_minor,
    sum(if(ev == 'deposit.settled', amt, 0)) as deposits_minor,
    sum(if(ev == 'deposit.declined' || ev == 'deposit.blocked', 1, 0)) as deposits_failed,
    sum(if(ev == 'withdrawal.paid', amt, 0)) as withdrawals_paid_minor,
    sum(if(ev == 'withdrawal.held', 1, 0)) as withdrawals_held,
    sum(if(ev == 'jackpot.won', amt, 0)) as jackpot_minor,
    approx_count_distinct({A}['session.id']) as sessions"""
SESSION_FUNNEL = f"""source logs
| filter $l.applicationname == 'coralogix-store' && {L}['event.domain'] == 'business' && {L}['session.id'] != null
| create ev from {L}['event.name']:string
| create amt from {_amt(L)}
| groupby {L}['session.id']:string as session_id aggregate
    min($m.timestamp) as first_seen,
    max({L}['user.id']:string) as user_id,
    max({L}['customer.type']:string) as customer_type,
    max({L}['user.segment']:string) as user_segment,
    sum(if(ev == 'basket.started', 1, 0)) as baskets_started,
    sum(if(ev == 'basket.started', amt, 0)) as basket_value_minor,
    max(if(ev == 'basket.abandoned', {L}['abandon.stage']:string, null)) as abandon_stage,
    sum(if(ev == 'order.placed', 1, 0)) as orders_placed,
    sum(if(ev == 'order.placed', amt, 0)) as order_value_minor,
    sum(if(ev == 'order.placed', {L}['order.discount_minor']:number, 0)) as discount_minor,
    max(if(ev == 'order.placed', {L}['order.promo_code']:string, null)) as promo_code,
    sum(if(ev == 'order.declined', 1, 0)) as orders_declined,
    sum(if(ev == 'order.lost', 1, 0)) as orders_lost,
    sum(if(ev == 'order.lost', amt, 0)) as lost_value_minor"""
CLIENT_DAILY = f"""source logs
| filter $l.applicationname == 'coralogix-finance' && ({L}['user.id'] != null)
| create ev from {L}['event.name']:string
| create amt from {_amt(L)}
| groupby roundTime($m.timestamp, 1d) as day, {L}['user.id']:string as user_id aggregate
    max({L}['user.segment']:string) as client_segment,
    sum(if(ev == 'query.billed', 1, 0)) as queries_billed,
    sum(if(ev == 'query.billed', amt, 0)) as billed_minor,
    sum(if(ev == 'query.failed', 1, 0)) as queries_failed,
    sum(if(ev == 'credit.refunded', amt, 0)) as refunded_minor,
    sum(if(ev == 'inference.charged', amt, 0)) as inference_cost_minor,
    sum(if(ev == 'vendor.datacall.charged', amt, 0)) as market_data_cost_minor,
    sum(if(ev == 'notebook.exported', 1, 0)) as notebooks_exported,
    sum(if({L}['error.type'] == 'VendorQuotaExceeded', 1, 0)) as vendor_quota_errors,
    sum(if($m.severity == ERROR, 1, 0)) as error_logs"""
SVC_LATENCY = """source spans
| filter $l.applicationName == 'goldspin-play' || $l.applicationName == 'coralogix-store' || $l.applicationName == 'coralogix-finance'
| create dur_ms from $m.duration:number / 1000
| groupby roundTime($m.timestamp, 1h) as hour, $l.applicationName as app, $l.serviceName as service, $l.operationName as operation, $d.tags['span.kind']:string as span_kind aggregate
    count() as spans,
    sum(if($d.tags['otel.status_code'] == 'ERROR', 1, 0)) as errors,
    avg(dur_ms) as avg_ms,
    percentile(0.5, dur_ms) as p50_ms,
    percentile(0.95, dur_ms) as p95_ms,
    percentile(0.99, dur_ms) as p99_ms,
    max(dur_ms) as max_ms"""
HTTP_HOURLY = f"""source logs
| filter ($l.subsystemname == 'edge-gateway' || $l.subsystemname == 'store-gateway' || $l.subsystemname == 'research-gateway')
| create route from firstNonNull({A}['http.route'], {L}['http.route'])
| create status from firstNonNull({A}['http.status_code'], {L}['http.status_code'])
| filter route != null && status != null
| groupby roundTime($m.timestamp, 1h) as hour, $l.applicationname as app, $l.subsystemname as gateway, route:string as route, status:string as status_code aggregate
    count() as requests"""
PRESSURE_HOURLY = f"""source logs
| filter $l.applicationname == 'goldspin-play' || $l.applicationname == 'coralogix-store' || $l.applicationname == 'coralogix-finance'
| create in_use from firstNonNull({A}['pool.in_use'], {L}['pool.in_use']):number
| create size from firstNonNull({A}['pool.size'], {L}['pool.size']):number
| create breached from firstNonNull({A}['budget.breached'], {L}['budget.breached']):string
| filter in_use != null || breached != null || $d ~ 'degraded'
| groupby roundTime($m.timestamp, 1h) as hour, $l.applicationname as app, $l.subsystemname as service aggregate
    sum(if(in_use != null, 1, 0)) as pool_samples,
    max(in_use) as pool_max_in_use,
    avg(in_use) as pool_avg_in_use,
    max(size) as pool_size,
    sum(if(in_use != null && in_use >= size, 1, 0)) as pool_saturated_samples,
    sum(if(breached == 'true', 1, 0)) as latency_budget_breaches,
    sum(if($d ~ 'degraded', 1, 0)) as degraded_pod_warnings"""
AI_CALLS = """source spans
| filter ($l.applicationName == 'goldspin-play' || $l.applicationName == 'coralogix-store' || $l.applicationName == 'coralogix-finance') && $d.tags['gen_ai.operation.name'] != null
| choose $l.applicationName as app, $l.serviceName as assistant, $d.tags['gen_ai.operation.name'] as operation, $d.tags['gen_ai.agent.name'] as agent_name, $d.tags['gen_ai.request.model'] as model, firstNonNull($d.tags['gen_ai.request.user'], $d.tags['enduser.id'], $d.tags['user.id']) as user_id, $d.tags['gen_ai.conversation.id'] as conversation_id, $d.traceID as trace_id, $d.tags['otel.status_code'] as status, $m.duration:number / 1000 as duration_ms, $d.tags['gen_ai.usage.input_tokens']:number as input_tokens, $d.tags['gen_ai.usage.output_tokens']:number as output_tokens, $d.tags['gen_ai.prompt_price']:number as prompt_cost_usd, $d.tags['gen_ai.response_price']:number as response_cost_usd, $d.tags['gen_ai.response.finish_reasons'] as finish_reasons, $d.tags['gen_ai.tool.name'] as tool_name, $d.tags['error'] as tool_error, $d.tags['guardrails.triggered'] as guardrail_triggered, $d.tags['gen_ai.prompt.guardrails.pii.triggered'] as guardrail_pii_triggered, $d.tags['gen_ai.prompt.guardrails.prompt_injection.triggered'] as guardrail_injection_triggered, $d.tags['gen_ai.prompt.guardrails.toxicity.triggered'] as guardrail_toxicity_triggered, $d.tags['gen_ai.prompt.guardrails.custom.0.name'] as guardrail_policy_name, $d.tags['gen_ai.prompt.guardrails.custom.0.triggered'] as guardrail_policy_triggered, $d.tags['gen_ai.prompt.evaluations.custom.0.name'] as eval0_name, $d.tags['gen_ai.prompt.evaluations.custom.0.triggered'] as eval0_triggered, $d.tags['gen_ai.prompt.evaluations.custom.1.name'] as eval1_name, $d.tags['gen_ai.prompt.evaluations.custom.1.triggered'] as eval1_triggered"""
FEED_HOURLY = f"""source logs
| filter $l.applicationname == 'goldspin-play' && {A}['feed.provider'] != null
| groupby roundTime($m.timestamp, 1h) as hour, $l.subsystemname as service, {A}['feed.provider']:string as provider aggregate
    count() as observations,
    sum(if({A}['feed.status'] == 'stale', 1, 0)) as stale_observations,
    max({A}['feed.last_tick_age_ms']:number) as max_tick_age_ms,
    avg({A}['feed.last_tick_age_ms']:number) as avg_tick_age_ms,
    approx_count_distinct(if({A}['market.status'] == 'suspended', {A}['market.id']:string, null)) as suspended_markets,
    sum(if({A}['error.type'] != null, 1, 0)) as errors"""

DATASETS = {
    'gaming.wallet_events': dict(query=GAMING, partition='$m.timestamp', chunks='daily',
                description='GoldSpin Play (goldspin-prod) business money events, one row per event: bets placed/accepted/rejected/won/lost, deposits settled/declined/blocked, withdrawals paid/held, jackpots. Flattened fields: event, outcome, service, user_id, user_segment, session_id, amount_minor (GBP pence), currency, direction, money_kind, margin_minor, payment_method, decline_code/issuer/reason, reject_reason, hold_reason, market, selection, price, error_type. Source: goldspin-play logs where event.domain = business.'),
    'gaming.player_daily': dict(query=PLAYER_DAILY, partition='day', chunks='week',
                description='GoldSpin Play per player per UTC day: stake, bets accepted/rejected, winnings paid, house margin (positive = house won), deposits settled/failed, withdrawals paid/held, jackpots, session count. All money fields in GBP pence.'),
    'gaming.odds_feed_hourly': dict(query=FEED_HOURLY, partition='hour', chunks='week',
                description='Hourly sportsbook price-feed health per service and feed provider: observations, stale observations, max/avg last-tick age in ms, suspended markets, errors.'),
    'ecommerce.order_events': dict(query=STORE, partition='$m.timestamp', chunks='daily',
                description='Coralogix Store (store-prod) commerce events, one row per event: basket.started/line/abandoned, order.placed/line/cogs/declined/lost. Flattened fields: event, outcome, service, user_id, user_segment, customer_type, session_id, amount_minor (GBP pence), direction, money_kind, order_items, order_lines, order_discount_minor, order_margin_minor, promo_code, delivery_method, payment_method, sku, product_name, department, line_qty, line_unit_price_minor, line_full_price_minor, line_discount_minor, line_revenue_minor, line_cost_minor, line_margin_minor, line_value_minor, basket_items, abandon_stage, stock_cause, decline_code, decline_reason. Source: coralogix-store logs where event.domain = business.'),
    'ecommerce.session_funnel': dict(query=SESSION_FUNNEL, partition='first_seen', chunks='week',
                description='Coralogix Store per shopping session: first_seen, user, customer_type, segment, baskets started and value, abandon_stage, orders placed/declined/lost with values, discount and promo code. Money in GBP pence.'),
    'fintech.research_billing': dict(query=FIN, partition='$m.timestamp', chunks='week',
                description='Coralogix Finance research platform (finance-prod) billing events, one row per event: query.billed, query.failed, inference.charged, vendor.datacall.charged, credit.refunded, notebook.exported. Flattened fields: event, outcome, service, user_id, client_segment, session_id, amount_minor (GBP pence), direction, cost_kind, asset_class, region, cited, citation_count, retrieval_passes, vendor_calls, guardrail_blocked, refund_reason, export_format. Source: coralogix-finance logs where event.domain = business.'),
    'fintech.client_daily': dict(query=CLIENT_DAILY, partition='day', chunks='week',
                description='Coralogix Finance per client user per UTC day: client_segment, queries billed/failed, billed and refunded value, inference and market-data cost, notebooks exported, vendor quota errors, error log count. Money in GBP pence.'),
    'platform.service_latency_hourly': dict(query=SVC_LATENCY, partition='hour', chunks='daily',
                description='Hourly span rollup for goldspin-play, coralogix-store and coralogix-finance: per app, service, operation and span kind, span count, error count, avg/p50/p95/p99/max latency in ms. Includes database (postgres) and cache (redis) client spans.'),
    'platform.http_requests_hourly': dict(query=HTTP_HOURLY, partition='hour', chunks='week',
                description='Hourly HTTP request counts at the edge for edge-gateway (goldspin-play), store-gateway (coralogix-store) and research-gateway (coralogix-finance), by route and status code.'),
    'platform.resource_pressure_hourly': dict(query=PRESSURE_HOURLY, partition='hour', chunks='daily',
                description='Hourly resource-pressure rollup per app and service: connection-pool samples, max/avg in use, pool size, saturated samples, latency-budget breaches and degraded-pod warnings.'),
    'ops.error_rollup_hourly': dict(query=ERR, partition='hour', chunks='week',
                description='Hourly rollup of WARNING-and-above log events for goldspin-play, coralogix-store and coralogix-finance. One row per hour x app x service x severity x error_type with an events count. error_type is the error.type attribute, or none when the log has no typed error.'),
    'ai.assistant_calls': dict(query=AI_CALLS, partition='$m.timestamp', chunks='week',
                description='One row per GenAI span from the customer-facing assistants (goldspin chat-assistant, store support-assistant, finance insight-assistant): operation, agent, model, user, conversation, status, duration, tokens, cost in USD, tool name and error, guardrail outcomes (PII, prompt injection, toxicity, custom policy) and evaluation triggers.'),
}
