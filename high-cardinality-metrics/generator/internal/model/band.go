// Package model defines the cardinality bands, their metric families,
// and the stateless series-identity derivation.
//
// The hard rule: a series is never stored. Every series identity is computed
// from its integer index by arithmetic, so band b6 (100,000,000 series) is
// described in a few hundred bytes and never materialised.
package model

import (
	"fmt"
	"strconv"
	"strings"
)

type FamilyKind string

const (
	FamilyCounter   FamilyKind = "counter"
	FamilyHistogram FamilyKind = "histogram"
	FamilyGauge     FamilyKind = "gauge"
	FamilyInfo      FamilyKind = "info"
)

// Fan-out constants. Changing any of these changes a
// band subtotal, and `metricgen plan` must exit non-zero when that happens.
const (
	CounterRoutes    = 10
	CounterMethods   = 5
	CounterStatuses  = 4
	CounterCustomers = 5
	CounterFanout    = CounterRoutes * CounterMethods * CounterStatuses * CounterCustomers // 1000

	HistogramRoutes            = 10
	HistogramBuckets           = 12
	HistogramSeriesPerLabelSet = HistogramBuckets + 2                         // 12 buckets + _sum + _count
	HistogramFanout            = HistogramRoutes * HistogramSeriesPerLabelSet // 140

	GaugeContainers = 4
	GaugeCustomers  = 5
	GaugeFanout     = GaugeContainers * GaugeCustomers // 20

	InfoFanout = 1
)

// GrandTarget is the sum of the band targets.
// The benchmark bands are b1 to b4: 100k/200k/500k/1M = 1,800,000.
// b5 (10M) and b6 (100M) are optional scale-up bands. They are never generated
// alongside b1 to b4: a scale-up run emits a single band on its own, so the
// corpus stays at the size being measured.
const GrandTarget = 111_800_000

type Family struct {
	Kind   FamilyKind
	Name   string // metric name including band suffix
	Type   string // Prometheus type string
	Pods   int64
	Fanout int64
}

// SeriesCount is the family's exact series count: pods x fan-out.
func (f Family) SeriesCount() int64 { return f.Pods * f.Fanout }

type Band struct {
	ID       string
	Target   int64
	Families []Family
}

func (b Band) Total() int64 {
	var t int64
	for _, f := range b.Families {
		t += f.SeriesCount()
	}
	return t
}

var bandTargets = []int64{100_000, 200_000, 500_000, 1_000_000, 10_000_000, 100_000_000}

// bandPods is the per-family pod count for each band,
// index order is [counter, histogram, gauge, info].
var bandPods = [6][4]int64{
	{60, 200, 500, 2_000},                 // b1    100,000
	{120, 400, 1_000, 4_000},              // b2    200,000
	{300, 1_000, 2_500, 10_000},           // b3    500,000
	{600, 2_000, 5_000, 20_000},           // b4  1,000,000
	{6_000, 20_000, 50_000, 200_000},      // b5  10,000,000
	{60_000, 200_000, 500_000, 2_000_000}, // b6 100,000,000
}

// Bands returns the six bands (b1 to b4 for this benchmark, b5 and b6 for scale-up runs). Each family's pod indices are 0..Pods-1, so
// the info family (largest pod count) is a superset of every other family's
// pods within a band, which is what the later counter-to-info join needs.
func Bands() []Band {
	bands := make([]Band, 0, len(bandTargets))
	for i, target := range bandTargets {
		id := fmt.Sprintf("b%d", i+1)
		pods := bandPods[i]
		bands = append(bands, Band{
			ID:     id,
			Target: target,
			Families: []Family{
				{
					Kind:   FamilyCounter,
					Name:   "http_requests_total_" + id,
					Type:   "counter",
					Pods:   pods[0],
					Fanout: CounterFanout,
				},
				{
					Kind:   FamilyHistogram,
					Name:   "http_request_duration_seconds_" + id,
					Type:   "histogram",
					Pods:   pods[1],
					Fanout: HistogramFanout,
				},
				{
					Kind:   FamilyGauge,
					Name:   "container_memory_working_set_bytes_" + id,
					Type:   "gauge",
					Pods:   pods[2],
					Fanout: GaugeFanout,
				},
				{
					Kind:   FamilyInfo,
					Name:   "kube_pod_info_" + id,
					Type:   "gauge",
					Pods:   pods[3],
					Fanout: InfoFanout,
				},
			},
		})
	}
	return bands
}

// SmokeBand is the deliberately small development corpus band: all four
// family shapes, ~1,200 series total, sized so the gauge family spans four
// distinct services (one series each) for independent cross-checks.
func SmokeBand() Band {
	return Band{
		ID:     "smoke",
		Target: 1_224,
		Families: []Family{
			{Kind: FamilyCounter, Name: "http_requests_total_smoke", Type: "counter", Pods: 1, Fanout: CounterFanout},
			{Kind: FamilyHistogram, Name: "http_request_duration_seconds_smoke", Type: "histogram", Pods: 1, Fanout: HistogramFanout},
			{Kind: FamilyGauge, Name: "container_memory_working_set_bytes_smoke", Type: "gauge", Pods: 4, Fanout: GaugeFanout},
			{Kind: FamilyInfo, Name: "kube_pod_info_smoke", Type: "gauge", Pods: 4, Fanout: InfoFanout},
		},
	}
}

// CalibBand is the cost-calibration preset: exactly 10,000 series with the
// same four family shapes at 60s. The factorisation below must total
// exactly 10,000; `plan` hard-fails otherwise.
func CalibBand() Band {
	return Band{
		ID:     "calib",
		Target: 10_000,
		Families: []Family{
			{Kind: FamilyCounter, Name: "http_requests_total_calib", Type: "counter", Pods: 6, Fanout: CounterFanout},
			{Kind: FamilyHistogram, Name: "http_request_duration_seconds_calib", Type: "histogram", Pods: 20, Fanout: HistogramFanout},
			{Kind: FamilyGauge, Name: "container_memory_working_set_bytes_calib", Type: "gauge", Pods: 50, Fanout: GaugeFanout},
			{Kind: FamilyInfo, Name: "kube_pod_info_calib", Type: "gauge", Pods: 200, Fanout: InfoFanout},
		},
	}
}

// Mix64 exposes the splitmix64 finaliser to the values package
// so every part of the generator hashes identically.
func Mix64(x uint64) uint64 { return mix64(x) }

// BucketUpperBound returns the le label value for histogram bucket sub
// (0-based). sub 11 is the +Inf bucket.
func BucketUpperBound(sub int) string { return bucketLE[sub] }

// Validate returns one error per band whose total is not exactly its target,
// plus one for the grand total if that is off. An empty slice means the
// factorisation is proven.
func Validate() []error {
	var errs []error
	var grand int64
	for _, b := range Bands() {
		total := b.Total()
		grand += total
		if total != b.Target {
			errs = append(errs, fmt.Errorf("band %s total %s != target %s", b.ID, Comma(total), Comma(b.Target)))
		}
	}
	if grand != GrandTarget {
		errs = append(errs, fmt.Errorf("grand total %s != target %s", Comma(grand), Comma(GrandTarget)))
	}
	return errs
}

// Comma formats n with thousands separators.
func Comma(n int64) string {
	s := strconv.FormatInt(n, 10)
	neg := strings.HasPrefix(s, "-")
	if neg {
		s = s[1:]
	}
	if len(s) > 3 {
		var b strings.Builder
		for i, c := range []byte(s) {
			if i > 0 && (len(s)-i)%3 == 0 {
				b.WriteByte(',')
			}
			b.WriteByte(c)
		}
		s = b.String()
	}
	if neg {
		return "-" + s
	}
	return s
}
