// Package values implements the value model: every series' value at a
// timestamp is a pure function of (seed, band, family, series index, ts).
// No accumulators, no per-series state, nothing carried between batches:
// series 73,000,001 at 10:05 is computable without ever having computed 10:04.
package values

import (
	"math"
	"strconv"

	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/model"
)

// Evaluator computes values. Every field is derived from its inputs; the
// methods are pure.
type Evaluator struct {
	Seed    uint64
	ResetAt int64 // unix seconds; counter series with index % ResetModulus == 0 restart from zero here
}

// ResetModulus is the counter-reset rule: within the counter family, the
// series whose family-local index satisfies index % ResetModulus == 0 reset
// once, at ResetAt, then climb again at the same rate.
const ResetModulus = 10_000

// FatTailModulus is the histogram fat-tail rule: label sets whose parent index
// (series index / 14) satisfies parent % FatTailModulus == 0 carry a markedly
// heavier tail.
const FatTailModulus = 1_000

// NewEvaluator builds an evaluator. resetAtUnix must lie strictly inside the window.
func NewEvaluator(seed uint64, resetAtUnix int64) *Evaluator {
	return &Evaluator{Seed: seed, ResetAt: resetAtUnix}
}

func fnv1a64(s string) uint64 {
	h := uint64(14695981039346656037)
	for i := 0; i < len(s); i++ {
		h ^= uint64(s[i])
		h *= 1099511628211
	}
	return h
}

func familyKey(seed uint64, bandID string, kind model.FamilyKind) uint64 {
	return model.Mix64(seed ^ fnv1a64(bandID+"/"+string(kind)))
}

// frac maps a uint64 to [0, 1) using the top 53 bits.
func frac(u uint64) float64 {
	return float64(u>>11) / float64(1<<53)
}

// Value returns the value of one series at one timestamp. Pure.
func (e *Evaluator) Value(bandID string, kind model.FamilyKind, seriesIndex int64, tsUnix int64) float64 {
	switch kind {
	case model.FamilyCounter:
		return e.counter(bandID, seriesIndex, tsUnix)
	case model.FamilyHistogram:
		return e.histogram(bandID, seriesIndex, tsUnix)
	case model.FamilyGauge:
		return e.gauge(bandID, seriesIndex, tsUnix)
	case model.FamilyInfo:
		return 1
	default:
		panic("unknown family kind " + string(kind))
	}
}

// CounterRate returns the per-series increment rate r (increments/sec) for a
// counter-family series. It is the quantity sum(rate(...)) converges to.
//
// r is full 53-bit precision on purpose: value(t) = floor(r*t + j) is computed
// against absolute unix timestamps (~1.79e9), so two series' value sequences
// stay identical only if their rates agree to ~1e-9, which requires their
// 53-bit hashes to collide. A coarse rate grid would do the opposite: with 60s
// sampling, frac(r*t) cycles through ~5 phase classes per window, and
// same-grid-rate series then share traces with probability ~1/6.
func (e *Evaluator) CounterRate(bandID string, seriesIndex int64) float64 {
	key := familyKey(e.Seed, bandID, model.FamilyCounter)
	h := model.Mix64(key ^ model.Mix64(uint64(seriesIndex)*2+1))
	return 0.1 + 49.9*frac(h)
}

// counter: value(t) = floor(r*(t - epoch) + j), where epoch is 0 or the reset
// time. Monotone because r > 0 and floor is monotone; the recorded reset
// series drop exactly once, at ResetAt, and climb again at the same rate.
func (e *Evaluator) counter(bandID string, seriesIndex int64, tsUnix int64) float64 {
	r := e.CounterRate(bandID, seriesIndex)
	key := familyKey(e.Seed, bandID, model.FamilyCounter)
	j := frac(model.Mix64(key ^ model.Mix64(uint64(seriesIndex)*2+2)))

	base := tsUnix
	if seriesIndex%ResetModulus == 0 && tsUnix >= e.ResetAt {
		base = tsUnix - e.ResetAt
	}
	return math.Floor(r*float64(base) + j)
}

// Histogram decomposition mirrors SeriesFor: within the family, parent label
// set = (pod, route) with 14 series each: 12 buckets, then _sum, then _count.
const histPerLabelSet = int64(model.HistogramSeriesPerLabelSet)

type histParams struct {
	countRate float64 // requests/sec; drives _count and every bucket
	median    float64 // log-normal median, seconds
	sigma     float64 // log-normal shape
	jitter    float64 // [0,1) offset so _count is not an exact multiple of the rate
	fatTail   bool
}

// histCount is the label set's cumulative observation count at ts, itself a
// monotone counter, same construction as the counter family.
func (e *Evaluator) histCount(p histParams, tsUnix int64) float64 {
	return math.Floor(p.countRate*float64(tsUnix) + p.jitter)
}

// histParamsFor derives one label set's latency profile from its parent index.
func (e *Evaluator) histParamsFor(bandID string, seriesIndex int64) (histParams, int) {
	parent := seriesIndex / histPerLabelSet
	sub := int(seriesIndex % histPerLabelSet)

	key := familyKey(e.Seed, bandID, model.FamilyHistogram)
	h := model.Mix64(key ^ model.Mix64(uint64(parent)*3+7))
	g := model.Mix64(key ^ model.Mix64(uint64(parent)*3+8))
	n := model.Mix64(key ^ model.Mix64(uint64(parent)*3+9))

	p := histParams{
		countRate: 0.2 + 29.8*frac(h),
		jitter:    frac(n),
		fatTail:   parent%FatTailModulus == 0,
	}
	if p.fatTail {
		p.median = 0.5 + 1.5*frac(g)
		p.sigma = 1.0 + 0.8*frac(g>>29)
	} else {
		p.median = 0.02 + 0.48*frac(g)
		p.sigma = 0.2 + 0.4*frac(g>>29)
	}
	return p, sub
}

func normalCDF(x float64) float64 {
	return 0.5 * (1 + math.Erf(x/math.Sqrt2))
}

func (e *Evaluator) histogram(bandID string, seriesIndex int64, tsUnix int64) float64 {
	p, sub := e.histParamsFor(bandID, seriesIndex)
	n := e.histCount(p, tsUnix)

	switch {
	case sub < model.HistogramBuckets:
		if sub == model.HistogramBuckets-1 {
			return n // the +Inf bucket equals _count exactly
		}
		le, err := strconv.ParseFloat(model.BucketUpperBound(sub), 64)
		if err != nil {
			panic("bad bucket upper bound: " + model.BucketUpperBound(sub))
		}
		z := (math.Log(le) - math.Log(p.median)) / p.sigma
		return math.Floor(float64(n) * normalCDF(z))

	case sub == model.HistogramBuckets: // _sum
		mean := p.median * math.Exp(p.sigma*p.sigma/2)
		return math.Floor(float64(n)*mean + 0.5)

	default: // _count
		return n
	}
}

// gauge: slow sine plus bounded noise in a plausible byte range. Per-series
// base, amplitude, period and phase; never monotonic.
func (e *Evaluator) gauge(bandID string, seriesIndex int64, tsUnix int64) float64 {
	key := familyKey(e.Seed, bandID, model.FamilyGauge)
	h := model.Mix64(key ^ model.Mix64(uint64(seriesIndex)*5+3))
	g := model.Mix64(key ^ model.Mix64(uint64(seriesIndex)*5+4))

	const mib = 1024 * 1024
	base := 200*mib + (6*1024*mib-200*mib)*frac(h)
	amp := 1*mib + 199*mib*frac(g)
	period := 1800 + 12600*frac(g>>29)
	phase := 2 * math.Pi * frac(g>>58)

	noise := (frac(model.Mix64(g^uint64(tsUnix))) - 0.5) * 2 * (amp / 4)
	return base + amp*math.Sin(2*math.Pi*float64(tsUnix)/period+phase) + noise
}
