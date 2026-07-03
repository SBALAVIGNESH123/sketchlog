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
	"sort"
	"strings"
	"sync"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/pdata/pcommon"
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
	cfg            Config
	client         *http.Client
	logger         *zap.Logger
	metricMap      map[string]MetricMapping
	cumulativeMu   sync.Mutex
	cumulativeSums map[string]float64
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
		cfg:            *scfg,
		client:         &http.Client{Timeout: scfg.Timeout},
		logger:         logger,
		metricMap:      metricMap,
		cumulativeSums: map[string]float64{},
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
				e.collectMetric(metric, mapping, batchFor(batches, mapping.Stream))
			}
		}
	}
	return e.flushBatches(ctx, batches)
}

func (e *sketchLogExporter) collectMetric(metric pmetric.Metric, mapping MetricMapping, batch *eventBatch) {
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
			if v, ok := numberValue(dps.At(i)); ok {
				addNumber(v)
			}
		}
	case pmetric.MetricTypeSum:
		sum := metric.Sum()
		dps := sum.DataPoints()
		for i := 0; i < dps.Len(); i++ {
			dp := dps.At(i)
			v, ok := numberValue(dp)
			if !ok {
				continue
			}
			switch sum.AggregationTemporality() {
			case pmetric.AggregationTemporalityCumulative:
				if delta, emit := e.cumulativeDelta(metric.Name(), dp.Attributes(), v); emit {
					addNumber(delta)
				}
			case pmetric.AggregationTemporalityDelta:
				addNumber(v)
			default:
				// Unspecified sum temporality has no safe delta semantics. Skip it
				// rather than risk double-counting cumulative counters.
				continue
			}
		}
	case pmetric.MetricTypeHistogram:
		dps := metric.Histogram().DataPoints()
		for i := 0; i < dps.Len(); i++ {
			expandHistogram(dps.At(i), e.cfg.MaxBatchItems, addNumber)
		}
	case pmetric.MetricTypeSummary:
		dps := metric.Summary().DataPoints()
		for i := 0; i < dps.Len(); i++ {
			if v, ok := summaryQuantile(dps.At(i), 0.95); ok {
				addNumber(v)
			}
		}
	}
}

func numberValue(dp pmetric.NumberDataPoint) (float64, bool) {
	switch dp.ValueType() {
	case pmetric.NumberDataPointValueTypeInt:
		return float64(dp.IntValue()), true
	case pmetric.NumberDataPointValueTypeDouble:
		return dp.DoubleValue(), true
	default:
		return 0, false
	}
}

func (e *sketchLogExporter) cumulativeDelta(metricName string, attrs pcommon.Map, current float64) (float64, bool) {
	key := timeseriesKey(metricName, attrs)
	e.cumulativeMu.Lock()
	defer e.cumulativeMu.Unlock()
	previous, seen := e.cumulativeSums[key]
	e.cumulativeSums[key] = current
	if !seen {
		// The first cumulative datapoint has no known baseline. Treat it as
		// state initialization, not an event delta, to avoid replaying a full
		// process lifetime counter into SketchLog.
		return 0, false
	}
	if current < previous {
		// Counter reset: emit the current value as the first value after reset.
		return current, current > 0
	}
	delta := current - previous
	return delta, delta > 0
}

func timeseriesKey(metricName string, attrs pcommon.Map) string {
	parts := make([]string, 0, attrs.Len()+1)
	parts = append(parts, metricName)
	attrs.Range(func(k string, v pcommon.Value) bool {
		parts = append(parts, k+"="+v.AsString())
		return true
	})
	sort.Strings(parts[1:])
	return strings.Join(parts, "|")
}

func expandHistogram(dp pmetric.HistogramDataPoint, maxSamples int, addNumber func(float64)) {
	count := dp.Count()
	if count == 0 {
		return
	}
	if maxSamples <= 0 {
		maxSamples = 1000
	}
	bucketCounts := dp.BucketCounts()
	bounds := dp.ExplicitBounds()
	if bucketCounts.Len() == 0 {
		if dp.HasSum() {
			addNumber(dp.Sum() / float64(count))
		}
		return
	}
	if count <= uint64(maxSamples) {
		for i := 0; i < bucketCounts.Len(); i++ {
			emitHistogramBucket(i, bucketCounts.At(i), bounds, addNumber)
		}
		return
	}
	// Histograms can contain very large cumulative counts. Preserve shape with a
	// bounded deterministic down-sample instead of appending one synthetic sample
	// per raw observation, which could otherwise exhaust collector memory.
	emitted := 0
	for i := 0; i < bucketCounts.Len(); i++ {
		c := bucketCounts.At(i)
		if c == 0 {
			continue
		}
		samples := int(math.Round(float64(c) / float64(count) * float64(maxSamples)))
		if samples == 0 {
			samples = 1
		}
		if emitted+samples > maxSamples {
			samples = maxSamples - emitted
		}
		if samples <= 0 {
			return
		}
		emitHistogramBucket(i, uint64(samples), bounds, addNumber)
		emitted += samples
	}
}

func emitHistogramBucket(bucket int, count uint64, bounds pcommon.Float64Slice, addNumber func(float64)) {
	if count == 0 {
		return
	}
	representative, ok := histogramRepresentative(bucket, bounds)
	if !ok {
		return
	}
	for j := uint64(0); j < count; j++ {
		addNumber(representative)
	}
}

func histogramRepresentative(bucket int, bounds pcommon.Float64Slice) (float64, bool) {
	boundCount := bounds.Len()
	switch {
	case boundCount == 0:
		return 0, false
	case bucket == 0:
		return bounds.At(0) / 2, true
	case bucket < boundCount:
		return (bounds.At(bucket-1) + bounds.At(bucket)) / 2, true
	case bucket == boundCount:
		return bounds.At(boundCount - 1), true
	default:
		return 0, false
	}
}

func summaryQuantile(dp pmetric.SummaryDataPoint, threshold float64) (float64, bool) {
	qvs := dp.QuantileValues()
	bestQuantile := math.Inf(1)
	bestValue := 0.0
	found := false
	for i := 0; i < qvs.Len(); i++ {
		qv := qvs.At(i)
		q := qv.Quantile()
		if q >= threshold && q < bestQuantile {
			bestQuantile = q
			bestValue = qv.Value()
			found = true
		}
	}
	return bestValue, found
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
			err := fmt.Errorf("sketchlog returned HTTP %d for stream %q: %s", resp.StatusCode, stream, strings.TrimSpace(string(responseBody)))
			if isPermanentHTTPStatus(resp.StatusCode) {
				return consumererror.NewPermanent(err)
			}
			return err
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
	if err := validateStreamPath(stream); err != nil {
		return "", err
	}
	u, err := url.Parse(e.cfg.Endpoint)
	if err != nil {
		return "", err
	}
	base := strings.TrimRight(u.EscapedPath(), "/")
	if base == "" {
		base = ""
	}
	ns := url.PathEscape(e.cfg.Namespace)
	escapedStream := escapeStreamPath(stream)
	u.Path = ""
	u.RawPath = base + "/v1/namespaces/" + ns + "/streams/" + escapedStream + "/events"
	u.Path, _ = url.PathUnescape(u.RawPath)
	return u.String(), nil
}

func validateStreamPath(stream string) error {
	if stream == "" || strings.HasPrefix(stream, "/") || strings.HasSuffix(stream, "/") {
		return fmt.Errorf("invalid stream path %q", stream)
	}
	for _, segment := range strings.Split(stream, "/") {
		if segment == "" || segment == "." || segment == ".." {
			return fmt.Errorf("invalid stream path segment %q in %q", segment, stream)
		}
	}
	return nil
}

func escapeStreamPath(stream string) string {
	segments := strings.Split(stream, "/")
	for i, segment := range segments {
		segments[i] = url.PathEscape(segment)
	}
	return strings.Join(segments, "/")
}

func isPermanentHTTPStatus(status int) bool {
	return status >= 400 && status < 500 && status != http.StatusTooManyRequests && status != http.StatusRequestTimeout
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
