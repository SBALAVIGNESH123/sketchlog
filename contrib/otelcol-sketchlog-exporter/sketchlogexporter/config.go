package sketchlogexporter

import (
	"errors"
	"fmt"
	"net/url"
	"time"
)

// SignalKind selects how an OpenTelemetry measurement is converted to SketchLog.
type SignalKind string

const (
	SignalLatency SignalKind = "latency"
	SignalEvent   SignalKind = "event"
	SignalUnique  SignalKind = "unique"
)

// MetricMapping maps one OTEL metric name into a SketchLog stream.
type MetricMapping struct {
	Name      string     `mapstructure:"name"`
	Stream    string     `mapstructure:"stream"`
	Kind      SignalKind `mapstructure:"kind"`
	EventName string     `mapstructure:"event_name"`
	Scale     float64    `mapstructure:"scale"`
}

// Config configures the SketchLog OpenTelemetry Collector exporter.
type Config struct {
	Endpoint           string          `mapstructure:"endpoint"`
	AuthToken          string          `mapstructure:"auth_token"`
	Namespace          string          `mapstructure:"namespace"`
	Timeout            time.Duration   `mapstructure:"timeout"`
	MaxBatchItems      int             `mapstructure:"max_batch_items"`
	Metrics            []MetricMapping `mapstructure:"metrics"`
	SpanDurationStream string          `mapstructure:"span_duration_stream"`
	LogEventStream     string          `mapstructure:"log_event_stream"`
}

func createDefaultConfig() Config {
	return Config{
		Endpoint:           "http://localhost:8000",
		Namespace:          "default",
		Timeout:            10 * time.Second,
		MaxBatchItems:      1000,
		SpanDurationStream: "otel.span.duration",
		LogEventStream:     "otel.logs",
	}
}

func (c *Config) Validate() error {
	if c.Endpoint == "" {
		return errors.New("endpoint is required")
	}
	parsed, err := url.Parse(c.Endpoint)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return fmt.Errorf("endpoint must be an absolute URL: %q", c.Endpoint)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("endpoint scheme must be http or https: %q", parsed.Scheme)
	}
	if c.Namespace == "" {
		return errors.New("namespace is required")
	}
	if c.Timeout <= 0 {
		return errors.New("timeout must be positive")
	}
	if c.MaxBatchItems <= 0 {
		return errors.New("max_batch_items must be positive")
	}
	if c.SpanDurationStream == "" && c.LogEventStream == "" && len(c.Metrics) == 0 {
		return errors.New("at least one metric mapping, span_duration_stream, or log_event_stream must be configured")
	}
	seen := map[string]struct{}{}
	for i, m := range c.Metrics {
		if m.Name == "" {
			return fmt.Errorf("metrics[%d].name is required", i)
		}
		if m.Stream == "" {
			return fmt.Errorf("metrics[%d].stream is required", i)
		}
		if _, ok := seen[m.Name]; ok {
			return fmt.Errorf("duplicate metric mapping for %q", m.Name)
		}
		seen[m.Name] = struct{}{}
		switch m.Kind {
		case SignalLatency, SignalEvent, SignalUnique:
		default:
			return fmt.Errorf("metrics[%d].kind must be one of latency, event, unique", i)
		}
		if m.Kind == SignalEvent && m.EventName == "" {
			return fmt.Errorf("metrics[%d].event_name is required for event mappings", i)
		}
	}
	return nil
}
