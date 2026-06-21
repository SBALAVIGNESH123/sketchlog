package sketchlog

import (
	"fmt"
	"time"
)

type SketchLogError struct {
	StatusCode int
	Message    string
}

func (e *SketchLogError) Error() string {
	return fmt.Sprintf("SketchLogError: %d - %s", e.StatusCode, e.Message)
}

type EventBatch struct {
	Latencies []float64          `json:"latencies,omitempty"`
	Uniques   []string           `json:"uniques,omitempty"`
	Events    map[string]int64   `json:"events,omitempty"`
}

// ClientOptions allows configuring the SketchLog client.
type ClientOptions struct {
	Endpoint   string
	MaxRetries int
	Timeout    time.Duration
}
