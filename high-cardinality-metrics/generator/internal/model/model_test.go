package model

import (
	"fmt"
	"testing"
)

func TestBandTotalsExact(t *testing.T) {
	// The six band targets.
	want := []int64{100_000, 200_000, 500_000, 1_000_000, 10_000_000, 100_000_000}
	bands := Bands()
	if len(bands) != len(want) {
		t.Fatalf("got %d bands, want %d", len(bands), len(want))
	}
	var grand int64
	for i, b := range bands {
		if got := b.Total(); got != want[i] {
			t.Errorf("band %s total = %d, want %d", b.ID, got, want[i])
		}
		grand += b.Total()
	}
	if grand != GrandTarget {
		t.Errorf("grand total = %d, want %d", grand, GrandTarget)
	}
}

func TestValidateClean(t *testing.T) {
	if errs := Validate(); len(errs) > 0 {
		t.Errorf("Validate returned errors on the correct tables: %v", errs)
	}
}

func TestCalibBandExactly10k(t *testing.T) {
	b := CalibBand()
	want := []int64{6_000, 2_800, 1_000, 200}
	for i, f := range b.Families {
		if got := f.SeriesCount(); got != want[i] {
			t.Errorf("calib family %s subtotal = %d, want %d", f.Name, got, want[i])
		}
	}
	if got := b.Total(); got != 10_000 {
		t.Errorf("calib total = %d, want exactly 10,000", got)
	}
}

func TestSmokeBandShape(t *testing.T) {
	b := SmokeBand()
	if got := b.Total(); got != 1_224 {
		t.Errorf("smoke band total = %d, want 1,224", got)
	}
}

func TestValidateCatchesCorruption(t *testing.T) {
	// Corrupt one pod count in b1 and Validate must report the band.
	saved := bandPods[0][0]
	defer func() { bandPods[0][0] = saved }()
	bandPods[0][0]++
	if errs := Validate(); len(errs) == 0 {
		t.Error("Validate reported no errors after corrupting a pod count")
	}
}

func TestSeriesDerivationStateless(t *testing.T) {
	bands := Bands()
	for _, b := range bands {
		for _, f := range b.Families {
			n := f.SeriesCount()
			if n > 5000 {
				n = 5000 // sample large families; derivation is index-uniform
			}
			for i := int64(0); i < n; i++ {
				a := SeriesFor(b, f, i)
				c := SeriesFor(b, f, n-i-1)
				d := SeriesFor(b, f, i)
				if seriesKey(a) != seriesKey(d) {
					t.Fatalf("band %s family %s: series %d not deterministic", b.ID, f.Name, i)
				}
				_ = c
			}
		}
	}
}

func seriesKey(s Series) string {
	k := s.Name
	for _, l := range s.Labels {
		k += "|" + l.Name + "=" + l.Value
	}
	return k
}

func TestCounterFanoutBijection(t *testing.T) {
	bands := Bands()
	b := bands[0]
	f := b.Families[0]
	seen := make(map[string]bool, CounterFanout)
	for i := int64(0); i < CounterFanout; i++ {
		s := SeriesFor(b, f, i)
		var route, method, status, cust string
		for _, l := range s.Labels {
			switch l.Name {
			case "http_route":
				route = l.Value
			case "http_method":
				method = l.Value
			case "status_code":
				status = l.Value
			case "customer_id":
				cust = l.Value
			}
		}
		k := route + method + status + cust
		if seen[k] {
			t.Fatalf("fan value %d produces duplicate label set %q", i, k)
		}
		seen[k] = true
	}
	if len(seen) != CounterFanout {
		t.Fatalf("got %d distinct label sets, want %d", len(seen), CounterFanout)
	}
}

func TestHistogramShape(t *testing.T) {
	bands := Bands()
	f := bands[0].Families[1]
	if f.Fanout != HistogramFanout {
		t.Fatalf("histogram fan-out = %d, want %d", f.Fanout, HistogramFanout)
	}
	les := make(map[string]bool)
	shapes := make(map[string]bool)
	for i := int64(0); i < HistogramFanout; i++ {
		s := SeriesFor(bands[0], f, i)
		switch {
		case s.Name == f.Name+"_bucket":
			for _, l := range s.Labels {
				if l.Name == "le" {
					les[l.Value] = true
				}
			}
		case s.Name == f.Name+"_sum":
			shapes["sum"] = true
		case s.Name == f.Name+"_count":
			shapes["count"] = true
		default:
			t.Fatalf("unexpected series name %q", s.Name)
		}
	}
	if len(les) != HistogramBuckets {
		t.Fatalf("got %d distinct le values, want %d", len(les), HistogramBuckets)
	}
	if !shapes["sum"] || !shapes["count"] {
		t.Fatalf("missing _sum or _count series: %v", shapes)
	}
}

func TestPodNamesDistinctAndStable(t *testing.T) {
	const sample = 10_000
	names := make(map[string]bool, sample)
	for i := int64(0); i < sample; i++ {
		a := DerivePod(i).Pod
		b2 := DerivePod(i).Pod
		if a != b2 {
			t.Fatalf("pod %d name not deterministic: %q vs %q", i, a, b2)
		}
		if names[a] {
			t.Fatalf("pod name collision at index %d: %q", i, a)
		}
		names[a] = true
	}
}

func TestInfoSupersetOfCounter(t *testing.T) {
	bands := Bands()
	b := bands[0]
	counter := b.Families[0]
	info := b.Families[3]

	// Every counter pod must have a matching info series (join key:
	// cluster, namespace, pod). Info fan-out is 1, so seriesIndex == podIndex.
	for pod := int64(0); pod < counter.Pods; pod++ {
		c := SeriesFor(b, counter, pod*counter.Fanout)
		i := SeriesFor(b, info, pod)
		get := func(s Series, name string) string {
			for _, l := range s.Labels {
				if l.Name == name {
					return l.Value
				}
			}
			return ""
		}
		for _, key := range []string{"cluster", "namespace", "pod"} {
			if get(c, key) != get(i, key) {
				t.Fatalf("info series for pod %d missing join key %s: %q vs %q",
					pod, key, get(c, key), get(i, key))
			}
		}
	}
}

func TestB5NeverMaterialised(t *testing.T) {
	// Derive series at extreme indices in the largest band. If the model
	// kept a map this would be where it exploded; it must stay O(1).
	bands := Bands()
	b5 := bands[4]
	for _, f := range b5.Families {
		last := f.SeriesCount() - 1
		s := SeriesFor(b5, f, last)
		if s.Name == "" {
			t.Fatalf("band b5 family %s: empty name at index %d", f.Name, last)
		}
	}
}

func TestFamilySeriesCountsMatchBrief(t *testing.T) {
	// b1 per-family subtotals, checked by hand:
	// 60x1000=60,000; 200x140=28,000; 500x20=10,000; 2000x1=2,000.
	want := []int64{60_000, 28_000, 10_000, 2_000}
	b1 := Bands()[0]
	for i, f := range b1.Families {
		if got := f.SeriesCount(); got != want[i] {
			t.Errorf("b1 family %s subtotal = %d, want %d", f.Name, got, want[i])
		}
	}
}

func ExampleComma() {
	fmt.Println(Comma(111_600_000))
	// Output: 111,600,000
}
