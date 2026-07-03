package sketchlogexporter

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer/consumererror"
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

func TestCumulativeSumsEmitOnlyDeltas(t *testing.T) {
	batch := &eventBatch{}
	cfg := createDefaultConfig()
	cfg.Metrics = []MetricMapping{{Name: "requests_total", Stream: "api.events", Kind: SignalEvent, EventName: "request"}}
	exp, err := newSketchLogExporter(&cfg, exporter.Settings{TelemetrySettings: component.TelemetrySettings{Logger: zaptest.NewLogger(t)}})
	if err != nil {
		t.Fatal(err)
	}
	metric := pmetric.NewMetric()
	metric.SetName("requests_total")
	sum := metric.SetEmptySum()
	sum.SetAggregationTemporality(pmetric.AggregationTemporalityCumulative)
	dp := sum.DataPoints().AppendEmpty()
	dp.SetIntValue(100)
	exp.collectMetric(metric, cfg.Metrics[0], batch)
	if len(batch.Events) != 0 {
		t.Fatalf("first cumulative sample should initialize baseline only, got %#v", batch.Events)
	}
	dp.SetIntValue(125)
	exp.collectMetric(metric, cfg.Metrics[0], batch)
	if got := batch.Events["request"]; got != 25 {
		t.Fatalf("expected delta 25, got %d", got)
	}
	dp.SetIntValue(7)
	exp.collectMetric(metric, cfg.Metrics[0], batch)
	if got := batch.Events["request"]; got != 32 {
		t.Fatalf("expected reset delta 7 added to 25, got %d", got)
	}
}

func TestUnsetNumberDataPointIsSkipped(t *testing.T) {
	batch := &eventBatch{}
	cfg := createDefaultConfig()
	cfg.Metrics = []MetricMapping{{Name: "latency", Stream: "api.latency", Kind: SignalLatency}}
	exp, err := newSketchLogExporter(&cfg, exporter.Settings{TelemetrySettings: component.TelemetrySettings{Logger: zaptest.NewLogger(t)}})
	if err != nil {
		t.Fatal(err)
	}
	metric := pmetric.NewMetric()
	metric.SetName("latency")
	metric.SetEmptyGauge().DataPoints().AppendEmpty()
	exp.collectMetric(metric, cfg.Metrics[0], batch)
	if len(batch.Latencies) != 0 {
		t.Fatalf("unset datapoint should be skipped, got %#v", batch.Latencies)
	}
}

func TestSummaryQuantileDoesNotAssumeOrdering(t *testing.T) {
	dp := pmetric.NewSummaryDataPoint()
	qvs := dp.QuantileValues()
	q99 := qvs.AppendEmpty()
	q99.SetQuantile(0.99)
	q99.SetValue(99)
	q95 := qvs.AppendEmpty()
	q95.SetQuantile(0.95)
	q95.SetValue(95)
	v, ok := summaryQuantile(dp, 0.95)
	if !ok || v != 95 {
		t.Fatalf("expected nearest quantile >= 0.95 to be 95, got %v ok=%v", v, ok)
	}
}

func TestHistogramExpansionPreservesShapeAndIsBounded(t *testing.T) {
	dp := pmetric.NewHistogramDataPoint()
	dp.SetCount(10000)
	dp.ExplicitBounds().Append(10, 20)
	dp.BucketCounts().Append(1000, 8000, 1000)
	values := []float64{}
	expandHistogram(dp, 10, func(v float64) { values = append(values, v) })
	if len(values) != 10 {
		t.Fatalf("expected bounded 10 synthetic samples, got %d", len(values))
	}
	seen := map[float64]bool{}
	for _, v := range values {
		seen[v] = true
	}
	if len(seen) < 2 {
		t.Fatalf("histogram expansion collapsed shape into one value: %#v", values)
	}
}

func TestStreamURLRejectsPathTraversalAndPreservesSegments(t *testing.T) {
	cfg := createDefaultConfig()
	cfg.Endpoint = "https://example.com/base/"
	cfg.Metrics = []MetricMapping{{Name: "latency", Stream: "api.latency", Kind: SignalLatency}}
	exp, err := newSketchLogExporter(&cfg, exporter.Settings{TelemetrySettings: component.TelemetrySettings{Logger: zaptest.NewLogger(t)}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := exp.streamURL("/absolute"); err == nil {
		t.Fatal("expected absolute stream path to be rejected")
	}
	if _, err := exp.streamURL("a/../b"); err == nil {
		t.Fatal("expected dot-segment stream path to be rejected")
	}
	got, err := exp.streamURL("service/api.latency")
	if err != nil {
		t.Fatal(err)
	}
	want := "https://example.com/base/v1/namespaces/default/streams/service/api.latency/events"
	if got != want {
		t.Fatalf("url mismatch\nwant %s\n got %s", want, got)
	}
}

func TestPermanentHTTPStatusUsesPermanentError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "bad token", http.StatusUnauthorized)
	}))
	defer srv.Close()
	cfg := createDefaultConfig()
	cfg.Endpoint = srv.URL
	cfg.Metrics = []MetricMapping{{Name: "latency", Stream: "api.latency", Kind: SignalLatency}}
	exp, err := newSketchLogExporter(&cfg, exporter.Settings{TelemetrySettings: component.TelemetrySettings{Logger: zaptest.NewLogger(t)}})
	if err != nil {
		t.Fatal(err)
	}
	err = exp.postBatch(context.Background(), "api.latency", &eventBatch{Latencies: []float64{1}})
	if err == nil {
		t.Fatal("expected unauthorized error")
	}
	if !consumererror.IsPermanent(err) {
		t.Fatalf("expected permanent error, got %T %v", err, err)
	}
}
