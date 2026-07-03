package sketchlogexporter

import (
	"context"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/exporter/exporterhelper"
)

const typeStr = "sketchlog"

// NewFactory creates a SketchLog exporter factory for metrics, traces, and logs.
func NewFactory() exporter.Factory {
	return exporter.NewFactory(
		component.MustNewType(typeStr),
		func() component.Config { cfg := createDefaultConfig(); return &cfg },
		exporter.WithMetrics(createMetricsExporter, component.StabilityLevelDevelopment),
		exporter.WithTraces(createTracesExporter, component.StabilityLevelDevelopment),
		exporter.WithLogs(createLogsExporter, component.StabilityLevelDevelopment),
	)
}

func createMetricsExporter(ctx context.Context, set exporter.Settings, cfg component.Config) (exporter.Metrics, error) {
	exp, err := newSketchLogExporter(cfg, set)
	if err != nil {
		return nil, err
	}
	return exporterhelper.NewMetrics(ctx, set, cfg, exp.consumeMetrics, exporterhelper.WithCapabilities(consumer.Capabilities{MutatesData: false}))
}

func createTracesExporter(ctx context.Context, set exporter.Settings, cfg component.Config) (exporter.Traces, error) {
	exp, err := newSketchLogExporter(cfg, set)
	if err != nil {
		return nil, err
	}
	return exporterhelper.NewTraces(ctx, set, cfg, exp.consumeTraces, exporterhelper.WithCapabilities(consumer.Capabilities{MutatesData: false}))
}

func createLogsExporter(ctx context.Context, set exporter.Settings, cfg component.Config) (exporter.Logs, error) {
	exp, err := newSketchLogExporter(cfg, set)
	if err != nil {
		return nil, err
	}
	return exporterhelper.NewLogs(ctx, set, cfg, exp.consumeLogs, exporterhelper.WithCapabilities(consumer.Capabilities{MutatesData: false}))
}
