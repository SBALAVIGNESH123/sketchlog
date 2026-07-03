package sketchlogexporter

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"path"
	"strings"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.uber.org/zap"
)

type eventBatch struct {
	Latencies []float64         `json:"latencies,omitempty"`
	Uniques   []string          `json:"uniques,omitempty"`
	Events    map[string]uint64 `json:"events,omitempty"`
}

type sketchLogExporter struct {
	cfg       Config
	client    *http.Client
	logger    *zap.Logger
	metricMap map[string]MetricMapping
}

func newSketchLogExporter(cfg component.Config, set exporter.Settings) (*sketchLogExporter, error) {
	scfg, ok := cfg.(*Config)
	if !ok {
		return nil, fmt.Errorf("invalid config type %T", cfg)
	}
	if err := scfg.Validate(); err != nil {
		return nil, err
	}
	metricMap := make(map[string]MetricMapping, len(scfg.Metrics))
	for _, m := range scfg.Metrics {
		if m.Scale == 0 {
			m.Scale = 1
		}
		metricMap[m.Name] = m
	}
	logger := set.Logger
	if logger == nil {
		logger = zap.NewNop()
	}
	return &sketchLogExporter{
		cfg:       *scfg,
		client:    &http.Client{Timeout: scfg.Timeout},
		logger:    logger,
		metricMap: metricMap,
	}, nil
}

func (e *sketchLogExporter) consumeMetrics(ctx context.Context, md pmetric.Metrics) error {
	batches := map[string]*eventBatch{}
	rms := md.ResourceMetrics()
	for i := 0; i < rms.Len(); i++ {
		sms := rms.At(i).ScopeMetrics()
		for j := 0; j < sms.Len(); j++ {
			ms := sms.At(j).Metrics()
			for k := 0; k < ms.Len(); k++ {
				metric := ms.At(k)
				mapping, ok := e.metricMap[metric.Name()]
				if !ok {
					continue
				}
				collectMetric(metric, mapping, batchFor(batches, mapping.Stream))
			}
		}
	}
	return e.flushBatches(ctx, batches)
}

func collectMetric(metric pmetric.Metric, mapping MetricMapping, batch *eventBatch) {
	scale := mapping.Scale
	if scale == 0 {
		scale = 1
	}
	addNumber := func(v float64) {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return
		}
		switch mapping.Kind {
		case SignalLatency:
			batch.Latencies = append(batch.Latencies, v*scale)
		case SignalUnique:
			batch.Uniques = append(batch.Uniques, fmt.Sprintf("%g", v))
		case SignalEvent:
			if v <= 0 {
				return
			}
			if batch.Events == nil {
				batch.Events = map[string]uint64{}
			}
			batch.Events[mapping.EventName] += uint64(math.Round(v))
		}
	}

	switch metric.Type() {
	case pmetric.MetricTypeGauge:
		dps := metric.Gauge().DataPoints()
		for i := 0; i < dps.Len(); i++ {
			addNumber(numberValue(dps.At(i)))
		}
	case pmetric.MetricTypeSum:
		dps := metric.Sum().DataPoints()
		for i := 0; i < dps.Len(); i++ {
			addNumber(numberValue(dps.At(i)))
		}
	case pmetric.MetricTypeHistogram:
		dps := metric.Histogram().DataPoints()
		for i := 0; i < dps.Len(); i++ {
			dp := dps.At(i)
			if dp.Count() == 0 {
				continue
			}
			addNumber(dp.Sum() / float64(dp.Count()))
		}
	case pmetric.MetricTypeSummary:
		dps := metric.Summary().DataPoints()
		for i := 0; i < dps.Len(); i++ {
			qvs := dps.At(i).QuantileValues()
			for q := 0; q < qvs.Len(); q++ {
				if qvs.At(q).Quantile() >= 0.95 {
					addNumber(qvs.At(q).Value())
					break
				}
			}
		}
	}
}

func numberValue(dp pmetric.NumberDataPoint) float64 {
	if dp.ValueType() == pmetric.NumberDataPointValueTypeInt {
		return float64(dp.IntValue())
	}
	return dp.DoubleValue()
}

func (e *sketchLogExporter) consumeTraces(ctx context.Context, td ptrace.Traces) error {
	stream := e.cfg.SpanDurationStream
	if stream == "" {
		return nil
	}
	batch := &eventBatch{}
	rts := td.ResourceSpans()
	for i := 0; i < rts.Len(); i++ {
		sss := rts.At(i).ScopeSpans()
		for j := 0; j < sss.Len(); j++ {
			spans := sss.At(j).Spans()
			for k := 0; k < spans.Len(); k++ {
				span := spans.At(k)
				start, end := span.StartTimestamp(), span.EndTimestamp()
				if end > start {
					batch.Latencies = append(batch.Latencies, float64(end-start)/float64(time.Millisecond))
				}
			}
		}
	}
	return e.postBatch(ctx, stream, batch)
}

func (e *sketchLogExporter) consumeLogs(ctx context.Context, ld plog.Logs) error {
	stream := e.cfg.LogEventStream
	if stream == "" {
		return nil
	}
	batch := &eventBatch{Events: map[string]uint64{}}
	rls := ld.ResourceLogs()
	for i := 0; i < rls.Len(); i++ {
		sls := rls.At(i).ScopeLogs()
		for j := 0; j < sls.Len(); j++ {
			logs := sls.At(j).LogRecords()
			for k := 0; k < logs.Len(); k++ {
				rec := logs.At(k)
				name := rec.EventName()
				if name == "" {
					name = rec.SeverityText()
				}
				if name == "" {
					name = "log_record"
				}
				batch.Events[safeName(name)]++
			}
		}
	}
	return e.postBatch(ctx, stream, batch)
}

func (e *sketchLogExporter) flushBatches(ctx context.Context, batches map[string]*eventBatch) error {
	for stream, batch := range batches {
		if err := e.postBatch(ctx, stream, batch); err != nil {
			return err
		}
	}
	return nil
}

func batchFor(batches map[string]*eventBatch, stream string) *eventBatch {
	b := batches[stream]
	if b == nil {
		b = &eventBatch{}
		batches[stream] = b
	}
	return b
}

func emptyBatch(b *eventBatch) bool {
	return b == nil || (len(b.Latencies) == 0 && len(b.Uniques) == 0 && len(b.Events) == 0)
}

func (e *sketchLogExporter) postBatch(ctx context.Context, stream string, batch *eventBatch) error {
	if emptyBatch(batch) {
		return nil
	}
	for _, chunk := range splitBatch(batch, e.cfg.MaxBatchItems) {
		body, err := json.Marshal(chunk)
		if err != nil {
			return err
		}
		endpoint, err := e.streamURL(stream)
		if err != nil {
			return err
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "application/json")
		if e.cfg.AuthToken != "" {
			req.Header.Set("X-SketchLog-Auth-Token", e.cfg.AuthToken)
		}
		resp, err := e.client.Do(req)
		if err != nil {
			return err
		}
		responseBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		resp.Body.Close()
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return fmt.Errorf("sketchlog returned HTTP %d for stream %q: %s", resp.StatusCode, stream, strings.TrimSpace(string(responseBody)))
		}
	}
	return nil
}

func splitBatch(batch *eventBatch, maxItems int) []*eventBatch {
	if maxItems <= 0 {
		maxItems = 1000
	}
	chunks := []*eventBatch{}
	current := &eventBatch{}
	currentItems := 0
	flush := func() {
		if !emptyBatch(current) {
			chunks = append(chunks, current)
		}
		current = &eventBatch{}
		currentItems = 0
	}
	ensure := func() {
		if currentItems >= maxItems {
			flush()
		}
	}
	for _, v := range batch.Latencies {
		ensure()
		current.Latencies = append(current.Latencies, v)
		currentItems++
	}
	for _, v := range batch.Uniques {
		ensure()
		current.Uniques = append(current.Uniques, v)
		currentItems++
	}
	for name, count := range batch.Events {
		if count == 0 {
			continue
		}
		ensure()
		if current.Events == nil {
			current.Events = map[string]uint64{}
		}
		current.Events[name] = count
		currentItems++
	}
	flush()
	return chunks
}

func (e *sketchLogExporter) streamURL(stream string) (string, error) {
	u, err := url.Parse(e.cfg.Endpoint)
	if err != nil {
		return "", err
	}
	ns := url.PathEscape(e.cfg.Namespace)
	escapedStream := strings.ReplaceAll(url.PathEscape(stream), "%2F", "/")
	u.Path = path.Join(strings.TrimRight(u.Path, "/"), "v1", "namespaces", ns, "streams", escapedStream, "events")
	return u.String(), nil
}

func safeName(s string) string {
	s = strings.TrimSpace(s)
	if len(s) > 255 {
		s = s[:255]
	}
	if s == "" {
		return "log_record"
	}
	return s
}
