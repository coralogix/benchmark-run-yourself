package main

import (
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/model"
)

const version = "0.1.0"

func main() {
	if len(os.Args) < 2 {
		usage(os.Stderr)
		os.Exit(2)
	}
	switch os.Args[1] {
	case "plan":
		if err := runPlan(os.Args[2:], os.Stdout); err != nil {
			fmt.Fprintln(os.Stderr, "metricgen: "+err.Error())
			os.Exit(1)
		}
	case "bench":
		if err := runBench(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, "metricgen: "+err.Error())
			os.Exit(1)
		}
	case "tsinsert":
		if err := runTSInsert(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, "metricgen: "+err.Error())
			os.Exit(1)
		}
	case "version", "--version", "-v":
		fmt.Println("metricgen " + version)
	default:
		fmt.Fprintf(os.Stderr, "metricgen: unknown command %q\n\n", os.Args[1])
		usage(os.Stderr)
		os.Exit(2)
	}
}

func usage(w io.Writer) {
	fmt.Fprintf(w, `usage: metricgen <command>

commands:
  plan      print the series factorisation proof for every band
  bench     stream the corpus to one or more OTLP endpoints at once
  tsinsert  load the corpus into a ClickHouse TimeSeries table
  version   print the version
`)
}

func runPlan(args []string, w io.Writer) error {
	preset := ""
	fs := flag.NewFlagSet("plan", flag.ContinueOnError)
	fs.StringVar(&preset, "preset", "", "validate a single preset instead of the bands: smoke | calib")
	if err := fs.Parse(args); err != nil {
		return err
	}

	fmt.Fprintln(w, "metricgen plan: series factorisation proof")
	fmt.Fprintln(w)
	fmt.Fprintf(w, "%-4s  %-10s  %-9s  %-44s  %10s  %9s  %-16s  %12s\n",
		"band", "family", "type", "metric name", "pods", "fan-out", "arithmetic", "subtotal")

	var target model.Band
	if preset == "" {
		bands := model.Bands()
		for _, b := range bands {
			printBand(w, b)
		}
		fmt.Fprintln(w)
		fmt.Fprintf(w, "Grand total: %s (target %s)\n", comma(grandTotal(bands)), comma(model.GrandTarget))
		if errs := model.Validate(); len(errs) > 0 {
			fmt.Fprintln(w)
			for _, err := range errs {
				fmt.Fprintf(w, "ERROR: %s\n", err)
			}
			fmt.Fprintln(w, "FAILED: band totals do not prove the factorisation.")
			return fmt.Errorf("%d band total check(s) failed", len(errs))
		}
		fmt.Fprintln(w, "OK: every band total exactly matches its target.")
		return nil
	}

	switch preset {
	case "smoke":
		target = model.SmokeBand()
	case "calib":
		target = model.CalibBand()
	default:
		return fmt.Errorf("unknown preset %q", preset)
	}
	printBand(w, target)
	if target.Total() != target.Target {
		return fmt.Errorf("preset %s total %s != target %s", target.ID, comma(target.Total()), comma(target.Target))
	}
	fmt.Fprintf(w, "OK: preset %s total exactly matches its target.\n", target.ID)
	return nil
}

func printBand(w io.Writer, b model.Band) {
	fmt.Fprintln(w)
	fmt.Fprintf(w, "Band %s, target %s\n", b.ID, comma(b.Target))
	for _, f := range b.Families {
		fmt.Fprintf(w, "  %-4s  %-10s  %-9s  %-44s  %10s  %9s  %-16s  %12s\n",
			b.ID,
			string(f.Kind),
			f.Type,
			f.Name,
			comma(f.Pods),
			comma(f.Fanout),
			fmt.Sprintf("%s x %s", comma(f.Pods), comma(f.Fanout)),
			comma(f.SeriesCount()),
		)
	}
	total := b.Total()
	status := "MATCH"
	if total != b.Target {
		status = "MISMATCH"
	}
	fmt.Fprintf(w, "  %-4s  %-10s  %-9s  %-44s  %10s  %9s  %-16s  %12s\n",
		b.ID, "total", "", "", "", "", "", comma(total))
	fmt.Fprintf(w, "  band %s total: %s, target: %s, %s\n", b.ID, comma(total), comma(b.Target), status)
}

func grandTotal(bands []model.Band) int64 {
	var t int64
	for _, b := range bands {
		t += b.Total()
	}
	return t
}

// comma re-exports the model's formatter for the plan table.
func comma(n int64) string { return model.Comma(n) }
