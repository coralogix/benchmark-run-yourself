## Operational tickets

### REL-1 · "Prices changed" error at checkout · P2
Support: "A few customers a day say checkout told them 'prices have changed, please review your basket',
but nothing changed. Seems to happen more with discount codes." No repro yet.

### REL-2 · Delivery slot page hangs · P2
On-call: "Every so often the slot picker spins forever and checkouts start failing. Restarting fulfilment
fixes it for a while. We've been doing that every morning."

### REL-3 · Double charges · P2
Finance: "Reconciliation found customers charged twice for one order. Seems to cluster on days the PSP
had a bad time. Refunding manually."

### REL-4 · Overselling promo items · P3
Warehouse: "We keep getting orders for promo items we don't have. Stock in the system says we did."

### REL-5 · Search sometimes very slow · P3
"Search is usually fine but sometimes it takes several seconds, and the product pages hang at the
same time."

---

## Tech debt

### TD-1 · Move storefront money to integer pence · P3
The storefront still stores and computes prices as float pounds. Everything new is in pence. Nobody has
had time.

### TD-2 · Replace the deprecated `request` library in fulfilment · P4
`request` has been deprecated since 2020. It is still used by the PSP adapter.
