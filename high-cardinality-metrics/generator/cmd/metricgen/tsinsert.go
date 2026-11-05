package main

// tsinsert: load the corpus into a ClickHouse TimeSeries table with native
// INSERTs, one row per series with its whole (timestamp, value) array. It uses
// model.SeriesFor and values.Evaluator, the same as bench, so metric names,
// label sets and the value model match the other loaders.
//
//   metricgen tsinsert --bands b1,b2,b3,b4 --backfill 168h --interval 1m \
//       --table prometheus.metrics --chunk 4000 --parallel 24
//
// Rows are streamed as JSONEachRow into `clickhouse-client` processes; the
// timestamp column (DateTime64(3)) is emitted in 'YYYY-MM-DD HH:MM:SS.mmm'
// form, the tags as a JSON object, samples as [[ts,value],...].

import (
	"bufio"
	"flag"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/model"
	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/values"
)

func runTSInsert(args []string) error {
	fs := flag.NewFlagSet("tsinsert", flag.ExitOnError)
	bandSel := fs.String("bands", "b1,b2,b3,b4", "comma-separated band ids")
	backfill := fs.Duration("backfill", 168*time.Hour, "history window ending now")
	interval := fs.Duration("interval", time.Minute, "sample interval")
	seed := fs.Uint64("seed", 1, "value-model seed (match the other engines)")
	table := fs.String("table", "prometheus.metrics", "target TimeSeries table")
	parallel := fs.Int("parallel", 16, "concurrent clickhouse-client inserters")
	chunk := fs.Int64("chunk", 4000, "series per INSERT")
	if err := fs.Parse(args); err != nil {
		return err
	}

	end := time.Now().UTC().Truncate(*interval)
	begin := end.Add(-*backfill)
	var tsStr []string
	var tsUnix []int64
	for t := begin; t.Before(end); t = t.Add(*interval) {
		tsStr = append(tsStr, t.Format("2006-01-02 15:04:05.000"))
		tsUnix = append(tsUnix, t.Unix())
	}
	// Reset anchor one hour before this process starts, as in bench.
	eval := values.NewEvaluator(*seed, time.Now().UTC().Add(-time.Hour).Unix())

	want := map[string]bool{}
	for _, id := range strings.Split(*bandSel, ",") {
		want[strings.TrimSpace(id)] = true
	}
	var bands []model.Band
	var totalSeries int64
	for _, b := range model.Bands() {
		if want[b.ID] {
			bands = append(bands, b)
			totalSeries += b.Total()
		}
	}
	fmt.Printf("tsinsert: %s series across %d bands, %d samples each, table %s, %d workers\n",
		model.Comma(totalSeries), len(bands), len(tsStr), *table, *parallel)

	type job struct {
		b      model.Band
		f      model.Family
		lo, hi int64
	}
	jobCh := make(chan job, 1024)
	go func() {
		for _, b := range bands {
			for _, f := range b.Families {
				n := f.SeriesCount()
				for lo := int64(0); lo < n; lo += *chunk {
					hi := lo + *chunk
					if hi > n {
						hi = n
					}
					jobCh <- job{b, f, lo, hi}
				}
			}
		}
		close(jobCh)
	}()

	var done int64
	var firstErr error
	var errMu sync.Mutex
	var wg sync.WaitGroup
	t0 := time.Now()
	for w := 0; w < *parallel; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range jobCh {
				if err := insertChunk(*table, j.b, j.f, j.lo, j.hi, tsStr, tsUnix, eval); err != nil {
					errMu.Lock()
					if firstErr == nil {
						firstErr = err
					}
					errMu.Unlock()
					return
				}
				n := atomic.AddInt64(&done, j.hi-j.lo)
				if n%200000 < (j.hi - j.lo) {
					el := time.Since(t0).Seconds()
					fmt.Printf("  %s/%s series, %.0fs, %.0f series/s\n",
						model.Comma(n), model.Comma(totalSeries), el, float64(n)/el)
				}
			}
		}()
	}
	wg.Wait()
	if firstErr != nil {
		return firstErr
	}
	fmt.Printf("tsinsert complete: %s series in %.0fs\n", model.Comma(done), time.Since(t0).Seconds())
	return nil
}

func insertChunk(table string, b model.Band, f model.Family, lo, hi int64,
	tsStr []string, tsUnix []int64, eval *values.Evaluator) error {
	cmd := exec.Command("clickhouse-client",
		"--query", "INSERT INTO "+table+" (metric_name, tags, samples) FORMAT JSONEachRow")
	pipe, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	bw := bufio.NewWriterSize(pipe, 1<<20)
	var sb strings.Builder
	for idx := lo; idx < hi; idx++ {
		s := model.SeriesFor(b, f, idx)
		sb.Reset()
		sb.WriteString(`{"metric_name":`)
		sb.WriteString(strconv.Quote(s.Name))
		sb.WriteString(`,"tags":{`)
		for i, l := range s.Labels {
			if i > 0 {
				sb.WriteByte(',')
			}
			sb.WriteString(strconv.Quote(l.Name))
			sb.WriteByte(':')
			sb.WriteString(strconv.Quote(l.Value))
		}
		sb.WriteString(`},"samples":[`)
		for i := range tsUnix {
			if i > 0 {
				sb.WriteByte(',')
			}
			v := eval.Value(b.ID, f.Kind, idx, tsUnix[i])
			sb.WriteString(`["`)
			sb.WriteString(tsStr[i])
			sb.WriteString(`",`)
			sb.WriteString(strconv.FormatFloat(v, 'f', -1, 64))
			sb.WriteByte(']')
		}
		sb.WriteString("]}\n")
		if _, err := bw.WriteString(sb.String()); err != nil {
			_ = cmd.Process.Kill()
			return err
		}
	}
	if err := bw.Flush(); err != nil {
		return err
	}
	if err := pipe.Close(); err != nil {
		return err
	}
	if err := cmd.Wait(); err != nil {
		return fmt.Errorf("insert %s[%d:%d]: %v: %s", f.Name, lo, hi, err, stderr.String())
	}
	return nil
}
