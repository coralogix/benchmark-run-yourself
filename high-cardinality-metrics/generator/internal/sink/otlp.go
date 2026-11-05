// Package sink: the OTLP/HTTP client. Every byte that goes on the wire is
// counted, so a backend's billed-byte figure can be compared with exactly
// what was sent.
package sink

import (
	"bytes"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"sort"
	"sync/atomic"
	"time"

	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/model"
	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/values"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
)

type TickSample struct {
	family model.Family
	name   string
	labels []model.Label
	value  float64
	isInt  bool
}

// BuildTick computes every sample of one tick for a band: family order, then
// series index ascending, values from the pure value model.
func BuildTick(band model.Band, eval *values.Evaluator, tsUnix int64) []TickSample {
	return BuildTickShard(band, eval, tsUnix, 0, 1)
}

// BuildTickShard computes only the samples belonging to `shard` of `nshards`.
//
// Two reasons this exists:
//
//  1. Sharding. Several writers send one corpus in parallel. Without this each
//     writer would emit the whole band and the backends would receive nshards
//     copies of every series.
//  2. Memory. BuildTick materialises the whole slice; at 100,000,000 series
//     that is tens of GB on one box. Each shard allocates only its own slice
//     and iterates only its own index range.
//
// Each family is split into contiguous ranges, so shard s of family f owns
// [s*N/nshards, (s+1)*N/nshards). Every series is emitted exactly once across
// the shard set.
func BuildTickShard(band model.Band, eval *values.Evaluator, tsUnix int64, shard, nshards int64) []TickSample {
	if nshards < 1 {
		nshards = 1
	}
	out := make([]TickSample, 0, band.Total()/nshards+16)
	for _, f := range band.Families {
		n := f.SeriesCount()
		lo := shard * n / nshards
		hi := (shard + 1) * n / nshards
		for idx := lo; idx < hi; idx++ {
			s := model.SeriesFor(band, f, idx)
			smp := TickSample{
				family: f,
				name:   s.Name,
				labels: s.Labels,
				value:  eval.Value(band.ID, f.Kind, idx, tsUnix),
			}
			if f.Kind != model.FamilyGauge {
				smp.isInt = true // counter, histogram and info values are integral
			}
			out = append(out, smp)
		}
	}
	return out
}

// buildRequest groups a batch of samples into one OTLP ExportRequest: one
// ResourceMetrics per distinct service (service.name is a mandatory resource
// attribute for Coralogix), one Metric per metric name, one NumberDataPoint
// per sample, in stable order. Both start_time_unix_nano and time_unix_nano
// are set on every datapoint. Returns the request and its datapoint count.
func buildRequest(samples []TickSample, tsUnix, startUnix int64) (pmetricotlp.ExportRequest, int) {
	serviceOf := func(s *TickSample) string {
		for _, l := range s.labels {
			if l.Name == "service" {
				return l.Value
			}
		}
		return ""
	}

	byService := map[string][]TickSample{}
	var services []string
	for i := range samples {
		svc := serviceOf(&samples[i])
		if _, seen := byService[svc]; !seen {
			services = append(services, svc)
			byService[svc] = nil
		}
		byService[svc] = append(byService[svc], samples[i])
	}
	sort.Strings(services)

	md := pmetric.NewMetrics()
	rms := md.ResourceMetrics()
	datapoints := 0
	for _, svc := range services {
		rm := rms.AppendEmpty()
		rm.Resource().Attributes().PutStr("service.name", svc)
		sm := rm.ScopeMetrics().AppendEmpty()

		metricIdx := map[string]pmetric.Metric{}
		for _, s := range byService[svc] {
			m, ok := metricIdx[s.name]
			if !ok {
				m = sm.Metrics().AppendEmpty()
				m.SetName(s.name)
				if s.family.Kind == model.FamilyCounter {
					sum := m.SetEmptySum()
					sum.SetAggregationTemporality(pmetric.AggregationTemporalityCumulative)
					sum.SetIsMonotonic(true)
				} else {
					m.SetEmptyGauge()
				}
				metricIdx[s.name] = m
			}
			var dps pmetric.NumberDataPointSlice
			if s.family.Kind == model.FamilyCounter {
				dps = m.Sum().DataPoints()
			} else {
				dps = m.Gauge().DataPoints()
			}
			dp := dps.AppendEmpty()
			attrs := dp.Attributes()
			for _, l := range s.labels {
				// Every label goes on the datapoint, INCLUDING service. It is
				// additionally set as the resource-level service.name above
				// because Coralogix requires that attribute, but resource
				// attributes are converted into a separate `target_info` series
				// rather than onto each metric series, so a resource-only
				// `service` is invisible to `sum by (service)` and
				// label/service/values returns []. Real exporters put the
				// job/service label on the series, and so does this one.
				attrs.PutStr(l.Name, l.Value)
			}
			dp.SetStartTimestamp(pcommon.Timestamp(startUnix) * 1e9)
			dp.SetTimestamp(pcommon.Timestamp(tsUnix) * 1e9)
			if s.isInt {
				dp.SetIntValue(int64(s.value))
			} else {
				dp.SetDoubleValue(s.value)
			}
			datapoints++
		}
	}
	return pmetricotlp.NewExportRequestFromMetrics(md), datapoints
}

type SendError struct {
	At      string `json:"at"`
	Status  int    `json:"status"`
	Body    string `json:"body_verbatim"`
	Samples int64  `json:"samples"`
}

// OTLPClient posts OTLP/HTTP export requests and counts every byte and every
// request, so the billed figure can be interpreted against exactly what was
// put on the wire. Uncompressed = the protobuf payload; CompressedBytes = the
// bytes actually transmitted (equal when gzip is off). Bytes are only counted
// for requests the endpoint accepted (2xx), so the accounting matches what
// landed; rejections are recorded separately.
type OTLPClient struct {
	// Endpoint is the single target, or the label used in logs when Endpoints
	// is set. Endpoints are several URLs written to round-robin.
	Endpoint  string
	Endpoints []string
	rr        uint64
	Key       string
	Client    *http.Client
	Gzip      bool

	SamplesSent       int64
	SamplesRejected   int64
	UncompressedBytes int64
	CompressedBytes   int64
	RequestsSent      int64
	RequestsRejected  int64
	Errors            []SendError
}

// SendBatch builds one export request from a batch of samples and posts it.
func (c *OTLPClient) SendBatch(samples []TickSample, tsUnix, startUnix int64) error {
	req, dps := buildRequest(samples, tsUnix, startUnix)

	body, err := req.MarshalProto()
	if err != nil {
		return fmt.Errorf("marshal OTLP request: %w", err)
	}
	uncompressed := len(body)

	wire := body
	if c.Gzip {
		var buf bytes.Buffer
		gz := gzip.NewWriter(&buf)
		if _, err := gz.Write(body); err != nil {
			return err
		}
		if err := gz.Close(); err != nil {
			return err
		}
		wire = buf.Bytes()
	}

	var lastErr error
	for attempt := 1; attempt <= 4; attempt++ {
		target := c.Endpoint
		if len(c.Endpoints) > 0 {
			target = c.Endpoints[int(atomic.AddUint64(&c.rr, 1)-1)%len(c.Endpoints)]
		}
		httpReq, err := http.NewRequest(http.MethodPost, target, bytes.NewReader(wire))
		if err != nil {
			return fmt.Errorf("build request: %w", err)
		}
		httpReq.Header.Set("Content-Type", "application/x-protobuf")
		if c.Gzip {
			httpReq.Header.Set("Content-Encoding", "gzip")
		}
		httpReq.Header.Set("Authorization", "Bearer "+c.Key)

		resp, err := c.Client.Do(httpReq)
		if err != nil {
			lastErr = fmt.Errorf("POST: %w", err)
		} else {
			respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
			resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				c.RequestsSent++
				c.SamplesSent += int64(dps)
				c.UncompressedBytes += int64(uncompressed)
				c.CompressedBytes += int64(len(wire))
				return nil
			}
			lastErr = fmt.Errorf("HTTP %d: %s", resp.StatusCode, respBody)
			// 4xx other than 429 is permanent.
			if resp.StatusCode != http.StatusTooManyRequests && resp.StatusCode < 500 {
				c.RequestsRejected++
				c.SamplesRejected += int64(dps)
				c.RecordError(resp.StatusCode, respBody, int64(dps))
				return fmt.Errorf("OTLP export rejected: %w", lastErr)
			}
		}
		if attempt < 4 {
			time.Sleep(time.Duration(1<<(attempt-1)) * time.Second)
		}
	}
	c.RequestsRejected++
	c.SamplesRejected += int64(0)
	c.RecordError(0, []byte(fmt.Sprintf("%v", lastErr)), 0)
	return fmt.Errorf("OTLP export failed after retries: %w", lastErr)
}

func (c *OTLPClient) RecordError(status int, body []byte, samples int64) {
	c.Errors = append(c.Errors, SendError{
		At:      time.Now().UTC().Format(time.RFC3339),
		Status:  status,
		Body:    string(body),
		Samples: samples,
	})
}
