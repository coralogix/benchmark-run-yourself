package values

import (
	"fmt"
	"math"
	"strconv"
	"testing"

	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/model"
)

const (
	testSeed    = uint64(1)
	testStart   = int64(1793145900) // fixed epoch for tests
	testTicks   = int64(100)
	testResetAt = testStart + 60*(testTicks*6/10)
)

func testEval() *Evaluator { return NewEvaluator(testSeed, testResetAt) }

// Acceptance 1: counter monotonicity for 1,000 sampled series over 100
// timestamps, with a drop exactly at the recorded reset and nowhere else.
func TestCounterMonotonicWithResets(t *testing.T) {
	band := model.Bands()[0] // b1
	f := band.Families[0]    // counter: 60,000 series
	_ = f

	e := testEval()
	seen := map[int64]bool{}
	// 1,000 deterministic samples spanning the family, plus every reset series.
	for i := 0; len(seen) < 1000; i++ {
		seen[int64(model.Mix64(uint64(i)*31+7)%uint64(f.SeriesCount()))] = true
	}
	for i := int64(0); i < f.SeriesCount(); i += ResetModulus {
		seen[i] = true
	}

	for idx := range seen {
		isReset := idx%ResetModulus == 0
		prev := e.Value(band.ID, model.FamilyCounter, idx, testStart)
		dropped := false
		for k := int64(1); k < testTicks; k++ {
			ts := testStart + k*60
			v := e.Value(band.ID, model.FamilyCounter, idx, ts)
			if v >= prev {
				prev = v
				continue
			}
			if !isReset || dropped {
				t.Fatalf("counter series %d decreased at ts %d: %v to %v", idx, ts, prev, v)
			}
			if ts != testResetAt {
				t.Fatalf("counter series %d dropped at ts %d, not at the recorded reset %d", idx, ts, testResetAt)
			}
			dropped = true
			prev = v
		}
		if isReset && !dropped {
			t.Fatalf("reset series %d never dropped its value", idx)
		}
	}
}

// histSeriesBase returns the family-local index of sub-series `sub` (0..13)
// of the parent label set (pod, route).
func histSeriesBase(pod, route int64) int64 {
	return pod*model.HistogramFanout + route*model.HistogramSeriesPerLabelSet
}

// Acceptance 2: buckets cumulative and non-decreasing across ascending le,
// +Inf equals _count, _sum plausible against _count. Checked for normal and
// fat-tail parents, at three timestamps.
func TestHistogramShape(t *testing.T) {
	band := model.Bands()[0]
	e := testEval()

	parents := []struct{ pod, route int64 }{
		{0, 0}, // parent 0, fat tail
		{3, 2},
		{7, 5},
		{199, 9},
		{0, 71}, // a normal-tail parent
		{100, 0},
	}

	for _, p := range parents {
		parentIdx := p.pod*(model.HistogramFanout/model.HistogramSeriesPerLabelSet) + p.route
		base := histSeriesBase(p.pod, p.route)
		for _, ts := range []int64{testStart, testStart + 3600, testStart + 86400} {
			var prevBucket float64
			for k := 0; k < model.HistogramBuckets; k++ {
				v := e.Value(band.ID, model.FamilyHistogram, base+int64(k), ts)
				if k > 0 && v < prevBucket {
					t.Fatalf("parent %d ts %d: bucket %d (%s) = %v < previous %v",
						parentIdx, ts, k, model.BucketUpperBound(k), v, prevBucket)
				}
				prevBucket = v
			}
			infBucket := e.Value(band.ID, model.FamilyHistogram, base+11, ts)
			count := e.Value(band.ID, model.FamilyHistogram, base+13, ts)
			sum := e.Value(band.ID, model.FamilyHistogram, base+12, ts)
			if infBucket != count {
				t.Fatalf("parent %d ts %d: +Inf bucket %v != _count %v", parentIdx, ts, infBucket, count)
			}
			if count > 0 {
				mean := sum / count
				if mean < 0.001 || mean > 120 {
					t.Fatalf("parent %d ts %d: implied mean latency %.4fs implausible (sum=%v count=%v)",
						parentIdx, ts, mean, sum, count)
				}
			}
		}
	}
}

// Acceptance 3: no two DISTINCT label
// sets may share a value trace. Duplicates within one histogram label set are
// expected (+Inf == _count by definition; buckets clamp to 0 or _count) and
// are covered by TestHistogramShape instead. The info family is exempt by
// definition and asserted separately in TestInfoAlwaysOne.
//
// For a histogram label set the trace used is its _count series: two label
// sets with identical full traces necessarily have identical _count traces, so
// _count distinctness across label sets is exactly the refined rule.
func TestValueTraceDistinctness(t *testing.T) {
	e := NewEvaluator(42, testStart+360)
	traces := make(map[string]string)

	// sampleN returns n distinct indices in [0, total), deterministically.
	sampleN := func(n, total int64, salt uint64) map[int64]bool {
		if n > total {
			n = total
		}
		seen := make(map[int64]bool, n)
		for i := int64(0); len(seen) < int(n); i++ {
			seen[int64(model.Mix64(uint64(i)*131+salt)%uint64(total))] = true
		}
		return seen
	}

	traceOf := func(bandID string, kind model.FamilyKind, idx int64) string {
		var trace string
		for k := int64(0); k < 20; k++ {
			v := e.Value(bandID, kind, idx, testStart+k*60)
			trace += strconv.FormatFloat(v, 'x', -1, 64) + ","
		}
		return trace
	}

	const perFamily = 3_400
	sampled := 0
	for _, band := range model.Bands() {
		for _, f := range band.Families {
			switch f.Kind {
			case model.FamilyCounter, model.FamilyGauge:
				// Every series is its own label set.
				for idx := range sampleN(perFamily, f.SeriesCount(), uint64(f.Fanout)) {
					tr := traceOf(band.ID, f.Kind, idx)
					if prev, dup := traces[tr]; dup {
						t.Fatalf("%s/%s series %d shares its 20-point trace with %s", band.ID, f.Kind, idx, prev)
					}
					traces[tr] = fmt.Sprintf("%s/%s:%d", band.ID, f.Kind, idx)
					sampled++
				}
			case model.FamilyHistogram:
				// Group by label set ignoring le: one parent = one label set,
				// represented by its _count series (see doc comment).
				parents := f.SeriesCount() / model.HistogramSeriesPerLabelSet
				for parent := range sampleN(perFamily, parents, uint64(f.Fanout)+1) {
					idx := parent*model.HistogramSeriesPerLabelSet + model.HistogramBuckets + 1
					tr := traceOf(band.ID, f.Kind, idx)
					if prev, dup := traces[tr]; dup {
						t.Fatalf("%s/histogram label set %d shares its 20-point trace with %s", band.ID, parent, prev)
					}
					traces[tr] = fmt.Sprintf("%s/histogram-parent:%d", band.ID, parent)
					sampled++
				}
			}
		}
	}
	if sampled < 10_000 {
		t.Fatalf("sampled %d label sets, expected at least 10,000", sampled)
	}
}

// Info series are always exactly 1, at every index and timestamp.
func TestInfoAlwaysOne(t *testing.T) {
	band := model.Bands()[4] // b5
	var f model.Family
	for _, fam := range band.Families {
		if fam.Kind == model.FamilyInfo {
			f = fam
		}
	}
	e := testEval()
	for i := int64(0); i < 1000; i++ {
		idx := int64(model.Mix64(uint64(i)*7) % uint64(f.SeriesCount()))
		ts := testStart + (idx%600)*60
		if v := e.Value(band.ID, model.FamilyInfo, idx, ts); v != 1 {
			t.Fatalf("info series value = %v, want exactly 1", v)
		}
	}
}

// Acceptance 4: values are identical whether computed in a batch of 1 or a
// batch of 100,000: the pure-function guarantee.
func TestValueBatchIndependence(t *testing.T) {
	band := model.Bands()[1] // b2
	e := testEval()

	type probe struct {
		kind model.FamilyKind
		idx  int64
		ts   int64
	}
	var probes []probe
	for _, f := range band.Families {
		for k := int64(0); k < 5; k++ {
			idx := int64(model.Mix64(uint64(k)*17+uint64(f.Fanout)) % uint64(f.SeriesCount()))
			probes = append(probes, probe{f.Kind, idx, testStart + (k%20)*60})
		}
	}

	// Batch of 1.
	alone := make([]float64, len(probes))
	for i, p := range probes {
		alone[i] = e.Value(band.ID, p.kind, p.idx, p.ts)
		if math.IsNaN(alone[i]) {
			t.Fatalf("probe %d produced NaN", i)
		}
	}

	// 100,000 unrelated calls in between, then the same inputs again: the
	// values must be bit-identical if there is truly no state.
	for j := int64(0); j < 100_000; j++ {
		f := band.Families[j%int64(len(band.Families))]
		e.Value(band.ID, f.Kind, j%f.SeriesCount(), testStart+j)
	}
	for i, p := range probes {
		after := e.Value(band.ID, p.kind, p.idx, p.ts)
		if after != alone[i] {
			t.Fatalf("probe %d (%s idx %d ts %d): %v before batch, %v after; value depends on call history",
				i, p.kind, p.idx, p.ts, alone[i], after)
		}
	}
}

// The gauge stays inside a plausible byte envelope and is not monotonic.
func TestGaugeBoundedAndNonMonotonic(t *testing.T) {
	band := model.SmokeBand()
	var f model.Family
	for _, fam := range band.Families {
		if fam.Kind == model.FamilyGauge {
			f = fam
		}
	}
	e := testEval()
	const giB = 1024 * 1024 * 1024
	rose := false
	for idx := int64(0); idx < f.SeriesCount(); idx++ {
		var prev float64
		for k := int64(0); k < 200; k++ {
			ts := testStart + k*60
			v := e.Value(band.ID, model.FamilyGauge, idx, ts)
			if v < 0 || v > 8*giB {
				t.Fatalf("gauge series %d value %v outside plausible byte range", idx, v)
			}
			if k > 0 && v != prev {
				rose = true
			}
			prev = v
		}
	}
	if !rose {
		t.Fatal("gauge values never varied")
	}
}

func Example() {
	e := NewEvaluator(1, 0)
	fmt.Println(e.Value("smoke", model.FamilyInfo, 0, 1793145900))
	// Output: 1
}
