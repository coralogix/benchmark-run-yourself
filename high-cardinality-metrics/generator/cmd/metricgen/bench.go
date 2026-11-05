package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/model"
	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/sink"
	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/values"
)

// runBench streams the selected bands to one or more OTLP endpoints. Every
// endpoint receives the same samples for the same timestamps.
func runBench(args []string) error {
	fs := flag.NewFlagSet("bench", flag.ExitOnError)
	var (
		targets  = fs.String("targets", "", "comma-separated name=url[,name=url]; key taken from <NAME>_KEY env, blank for none")
		duration = fs.Duration("duration", 24*time.Hour, "run duration")
		interval = fs.Duration("interval", time.Minute, "tick interval")
		batch    = fs.Int("batch", 2500, "samples per OTLP request")
		seed     = fs.Uint64("seed", 1, "corpus seed")
		outDir   = fs.String("out", "results/bench", "accounting directory")
		once     = fs.Bool("once", false, "send a single tick and exit")
		bandSel  = fs.String("bands", "", "comma-separated band ids to emit, e.g. b5. Empty = all bands")
		shard    = fs.Int64("shard", 0, "this generator's shard index, 0-based")
		shards   = fs.Int64("shards", 1, "total number of generator shards")
		minTick  = fs.Duration("backfill-min-tick", 0, "minimum wall time per backfill tick, so the backfill is written at a steady rate. 0 = unpaced.")
		backfill = fs.Duration("backfill", 0, "write this much HISTORY as fast as possible, "+
			"timestamps ascending from now-<backfill> to now, then stop. 0 = normal realtime run")
	)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *targets == "" {
		return fmt.Errorf("--targets is required, e.g. --targets 'coralogix=https://ingress.<domain>:443/v1/metrics,mimir=http://<mimir-node>:8080/otlp/v1/metrics'")
	}

	type target struct {
		name   string
		client *sink.OTLPClient
	}
	var tgts []*target
	for _, spec := range strings.Split(*targets, ",") {
		parts := strings.SplitN(strings.TrimSpace(spec), "=", 2)
		if len(parts) != 2 {
			return fmt.Errorf("bad --targets entry %q, want name=url", spec)
		}
		name, url := parts[0], parts[1]
		// A target may list several URLs separated by '|', written round-robin.
		var endpoints []string
		if strings.Contains(url, "|") {
			endpoints = strings.Split(url, "|")
			url = endpoints[0]
		}
		// Key convention: CORALOGIX_KEY, MIMIR_KEY, ... Absent = no auth header.
		key := os.Getenv(strings.ToUpper(name) + "_KEY")
		tgts = append(tgts, &target{name: name, client: &sink.OTLPClient{
			Endpoint:  url,
			Endpoints: endpoints,
			Key:       key,
			Gzip:      true,
			Client:    &http.Client{Timeout: 60 * time.Second},
		}})
		if len(endpoints) > 1 {
			fmt.Printf("target %-12s %d endpoints, round-robin\n", name, len(endpoints))
		}
		fmt.Printf("target %-12s %s  (auth: %s)\n", name, url,
			map[bool]string{true: fmt.Sprintf("bearer, key length %d", len(key)), false: "none"}[key != ""])
	}

	bands := model.Bands()
	// --bands selects which bands to emit. A scale-up run emits one band on
	// its own, so the corpus has only the cardinality being measured.
	if *bandSel != "" {
		want := map[string]bool{}
		for _, id := range strings.Split(*bandSel, ",") {
			want[strings.TrimSpace(id)] = true
		}
		var keep []model.Band
		for _, b := range bands {
			if want[b.ID] {
				keep = append(keep, b)
			}
		}
		if len(keep) == 0 {
			return fmt.Errorf("--bands %q matched no band", *bandSel)
		}
		bands = keep
	}
	var totalSeries int64
	for _, b := range bands {
		totalSeries += b.Total()
	}
	runID := fmt.Sprintf("bench-s%d-%s", *seed, time.Now().UTC().Format("20060102T150405Z"))
	if *shards < 1 || *shard < 0 || *shard >= *shards {
		return fmt.Errorf("bad shard %d of %d", *shard, *shards)
	}
	fmt.Printf("run %s: %s series across %d bands, shard %d/%d, interval %s, duration %s\n",
		runID, model.Comma(totalSeries), len(bands), *shard, *shards, *interval, *duration)

	eval := values.NewEvaluator(*seed, time.Now().UTC().Add(-time.Hour).Unix())
	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		return err
	}

	deadline := time.Now().Add(*duration)
	ticker := time.NewTicker(*interval)
	defer ticker.Stop()

	tick := func(ts time.Time) {
		start := ts.Add(-time.Hour).Unix()
		var wg sync.WaitGroup
		for _, b := range bands {
			samples := sink.BuildTickShard(b, eval, ts.Unix(), *shard, *shards)
			for i := 0; i < len(samples); i += *batch {
				end := i + *batch
				if end > len(samples) {
					end = len(samples)
				}
				chunk := samples[i:end]
				// Same chunk to every target, in parallel.
				for _, t := range tgts {
					wg.Add(1)
					go func(t *target, chunk []sink.TickSample) {
						defer wg.Done()
						_ = t.client.SendBatch(chunk, ts.Unix(), start)
					}(t, chunk)
				}
			}
		}
		wg.Wait()
		var parts []string
		for _, t := range tgts {
			parts = append(parts, fmt.Sprintf("%s: %s sent / %s rejected, %d reqs, %d req-errs",
				t.name, model.Comma(t.client.SamplesSent), model.Comma(t.client.SamplesRejected),
				t.client.RequestsSent, t.client.RequestsRejected))
		}
		fmt.Printf("[%s] tick %s | %s\n", time.Now().UTC().Format(time.RFC3339), ts.Format(time.RFC3339), strings.Join(parts, " | "))
	}

	flush := func() {
		acc := map[string]any{
			"run_id": runID, "seed": *seed, "series": totalSeries,
			"interval": interval.String(), "finished": time.Now().UTC().Format(time.RFC3339),
		}
		per := map[string]any{}
		for _, t := range tgts {
			per[t.name] = map[string]any{
				"endpoint": t.client.Endpoint, "samples_sent": t.client.SamplesSent,
				"samples_rejected": t.client.SamplesRejected, "requests_sent": t.client.RequestsSent,
				"requests_rejected":  t.client.RequestsRejected,
				"uncompressed_bytes": t.client.UncompressedBytes, "wire_bytes": t.client.CompressedBytes,
				"errors": t.client.Errors,
			}
		}
		acc["targets"] = per
		b, _ := json.MarshalIndent(acc, "", "  ")
		_ = os.WriteFile(filepath.Join(*outDir, runID+".json"), b, 0o644)
	}

	// Backfill: write history as fast as the backends will take it.
	//
	// Timestamps ascend from now-backfill to now, one interval apart, and ticks
	// are issued back to back with no wait. Because each series' samples still
	// arrive in ascending time order, this is an in-order write per series,
	// not an out-of-order append, so no out-of-order window is needed on
	// the receiving end. Parallelism comes from running shards on separate
	// boxes, not from overlapping ticks, which would break that ordering.
	if *backfill > 0 {
		begin := time.Now().UTC().Add(-*backfill).Truncate(*interval)
		end := time.Now().UTC().Truncate(*interval)
		total := int(end.Sub(begin) / *interval)
		fmt.Printf("backfill: %d ticks from %s to %s (%s of history)\n",
			total, begin.Format(time.RFC3339), end.Format(time.RFC3339), *backfill)
		t0 := time.Now()
		n := 0
		for ts := begin; ts.Before(end); ts = ts.Add(*interval) {
			tstart := time.Now()
			tick(ts)
			// Pacing: hold a floor on each tick's duration (--backfill-min-tick), so the
			// backfill is written at a steady rate.
			if *minTick > 0 {
				if d := time.Since(tstart); d < *minTick {
					time.Sleep(*minTick - d)
				}
			}
			n++
			if n%10 == 0 {
				el := time.Since(t0).Seconds()
				fmt.Printf("  backfill %d/%d ticks, %.0fs elapsed, %.1f ticks/s, eta %.0fs\n",
					n, total, el, float64(n)/el, (float64(total-n))/(float64(n)/el))
				flush()
			}
		}
		flush()
		fmt.Printf("backfill complete: %d ticks in %.0fs\n", n, time.Since(t0).Seconds())
		return nil
	}

	tick(time.Now().UTC().Truncate(*interval))
	if *once {
		flush()
		return nil
	}
	for range ticker.C {
		tick(time.Now().UTC().Truncate(*interval))
		flush()
		if time.Now().After(deadline) {
			break
		}
	}
	flush()
	fmt.Printf("run %s complete\n", runID)
	return nil
}
