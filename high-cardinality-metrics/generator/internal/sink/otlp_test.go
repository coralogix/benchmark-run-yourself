package sink

import (
	"compress/gzip"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/model"
	"github.com/coralogix-labs/benchmark-run-yourself/high-cardinality-metrics/generator/internal/values"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
)

// audit is what the loopback endpoint records about one received request.
type audit struct {
	unmarshalErr  error
	datapoints    int
	badTimestamps int
	noServiceName int
}

// auditRequest unmarshals and inspects one export payload.
func auditRequest(raw []byte) audit {
	a := audit{}
	exp := pmetricotlp.NewExportRequest()
	if err := exp.UnmarshalProto(raw); err != nil {
		a.unmarshalErr = err
		return a
	}
	rms := exp.Metrics().ResourceMetrics()
	for i := 0; i < rms.Len(); i++ {
		rm := rms.At(i)
		if _, ok := rm.Resource().Attributes().Get("service.name"); !ok {
			a.noServiceName++
		}
		sms := rm.ScopeMetrics()
		for j := 0; j < sms.Len(); j++ {
			ms := sms.At(j).Metrics()
			for k := 0; k < ms.Len(); k++ {
				dps := dataPoints(ms.At(k))
				for d := 0; d < dps.Len(); d++ {
					dp := dps.At(d)
					if dp.StartTimestamp() == 0 || dp.Timestamp() == 0 {
						a.badTimestamps++
					}
					a.datapoints++
				}
			}
		}
	}
	return a
}

func dataPoints(m pmetric.Metric) pmetric.NumberDataPointSlice {
	switch m.Type() {
	case pmetric.MetricTypeGauge:
		return m.Gauge().DataPoints()
	case pmetric.MetricTypeSum:
		return m.Sum().DataPoints()
	default:
		return pmetric.NewNumberDataPointSlice()
	}
}

// The instrument check before any real send: one full calib tick must arrive
// as exactly 10,000 datapoints, every one carrying both timestamps, grouped
// under service.name resource attributes; the sender's uncompressed-byte
// count must equal the payload the endpoint actually decoded.
func TestCalibTickShapeAndAccounting(t *testing.T) {
	band := model.CalibBand()
	eval := values.NewEvaluator(1, 0)
	ts := int64(1793145600)

	samples := BuildTick(band, eval, ts)
	if len(samples) != 10_000 {
		t.Fatalf("tick built %d samples, want 10,000", len(samples))
	}

	var gotRaw []byte
	var gotAudit audit
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Content-Type"); got != "application/x-protobuf" {
			t.Errorf("Content-Type = %q", got)
		}
		if got := r.Header.Get("Content-Encoding"); got != "gzip" {
			t.Errorf("Content-Encoding = %q, want gzip", got)
		}
		if r.Header.Get("Authorization") == "" {
			t.Error("missing Authorization header")
		}
		zr, err := gzip.NewReader(r.Body)
		if err != nil {
			t.Errorf("body not gzip: %v", err)
			http.Error(w, "not gzip", http.StatusBadRequest)
			return
		}
		raw, err := io.ReadAll(zr)
		if err != nil {
			t.Errorf("gunzip: %v", err)
			http.Error(w, "gunzip error", http.StatusBadRequest)
			return
		}
		gotRaw = raw
		gotAudit = auditRequest(raw)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	client := &OTLPClient{Endpoint: srv.URL, Key: "cxtp_dummy", Gzip: true, Client: &http.Client{}}
	if err := client.SendBatch(samples, ts, ts); err != nil {
		t.Fatalf("SendBatch: %v", err)
	}

	if gotAudit.datapoints != 10_000 {
		t.Errorf("endpoint decoded %d datapoints, want 10,000", gotAudit.datapoints)
	}
	if client.SamplesSent != 10_000 {
		t.Errorf("sender counted %d samples, want 10,000", client.SamplesSent)
	}
	if gotAudit.badTimestamps != 0 {
		t.Errorf("%d datapoints missing start_time or time", gotAudit.badTimestamps)
	}
	if gotAudit.noServiceName != 0 {
		t.Errorf("%d resources without service.name", gotAudit.noServiceName)
	}
	if int64(len(gotRaw)) != client.UncompressedBytes {
		t.Errorf("uncompressed accounting: sender %d, endpoint saw %d", client.UncompressedBytes, len(gotRaw))
	}
	if client.CompressedBytes == 0 || client.CompressedBytes >= client.UncompressedBytes {
		t.Errorf("wire bytes %d not compressed vs uncompressed %d", client.CompressedBytes, client.UncompressedBytes)
	}
}
