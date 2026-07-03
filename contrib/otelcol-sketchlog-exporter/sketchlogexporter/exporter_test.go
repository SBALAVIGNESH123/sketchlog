package sketchlogexporter

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.uber.org/zap/zaptest"
)

func TestConfigValidate(t *testing.T) {
	cfg := createDefaultConfig()
	cfg.Endpoint = "http://localhost:8000"
	cfg.Metrics = []MetricMapping{{Name: "http.server.duration", Stream: "api.latency", Kind: SignalLatency}}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate failed: %v", err)
	}
	cfg.Metrics[0].Kind = "bad"
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected invalid kind error")
	}
}

func TestConsumeMetricsPostsSketchLogBatch(t *testing.T) {
	var gotPath, gotToken string
	var got eventBatch
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotToken = r.Header.Get("X-SketchLog-Auth-Token")
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Fatal(err)
		}
		w.WriteHeader(http.StatusAccepted)
	}))
	defer srv.Close()

	cfg := createDefaultConfig()
	cfg.Endpoint = srv.URL
	cfg.AuthToken = "secret"
	cfg.Namespace = "prod"
	cfg.Metrics = []MetricMapping{{Name: "http.server.duration", Stream: "api.latency", Kind: SignalLatency, Scale: 1000}}
	exp, err := newSketchLogExporter(&cfg, exporter.Settings{TelemetrySettings: component.TelemetrySettings{Logger: zaptest.NewLogger(t)}})
	if err != nil {
		t.Fatal(err)
	}

	md := pmetric.NewMetrics()
	metric := md.ResourceMetrics().AppendEmpty().ScopeMetrics().AppendEmpty().Metrics().AppendEmpty()
	metric.SetName("http.server.duration")
	metric.SetEmptyGauge()
	dp := metric.Gauge().DataPoints().AppendEmpty()
	dp.SetDoubleValue(0.123)
	if err := exp.consumeMetrics(context.Background(), md); err != nil {
		t.Fatal(err)
	}
	if gotPath != "/v1/namespaces/prod/streams/api.latency/events" {
		t.Fatalf("path %q", gotPath)
	}
	if gotToken != "secret" {
		t.Fatalf("token %q", gotToken)
	}
	if len(got.Latencies) != 1 || got.Latencies[0] != 123 {
		t.Fatalf("latencies %#v", got.Latencies)
	}
}

func TestConsumeTracesAndLogs(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(http.StatusAccepted) }))
	defer srv.Close()
	cfg := createDefaultConfig()
	cfg.Endpoint = srv.URL
	exp, err := newSketchLogExporter(&cfg, exporter.Settings{TelemetrySettings: component.TelemetrySettings{Logger: zaptest.NewLogger(t)}})
	if err != nil {
		t.Fatal(err)
	}

	td := ptrace.NewTraces()
	span := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Unix(0, 0)))
	span.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Unix(0, int64(25*time.Millisecond))))
	if err := exp.consumeTraces(context.Background(), td); err != nil {
		t.Fatal(err)
	}

	ld := plog.NewLogs()
	rec := ld.ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords().AppendEmpty()
	rec.SetEventName("checkout_failed")
	if err := exp.consumeLogs(context.Background(), ld); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("expected 2 calls, got %d", calls)
	}
}

func TestSplitBatchRespectsMaxItems(t *testing.T) {
	batch := &eventBatch{Latencies: []float64{1, 2, 3}, Uniques: []string{"a", "b"}, Events: map[string]uint64{"error": 4}}
	chunks := splitBatch(batch, 2)
	if len(chunks) != 3 {
		t.Fatalf("expected 3 chunks, got %d", len(chunks))
	}
}
