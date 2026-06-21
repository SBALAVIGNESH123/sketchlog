package main

import (
	"context"
	"flag"
	"fmt"
	"os"

	"github.com/SBALAVIGNESH123/sketchlog-go"
)

func main() {
	endpoint := flag.String("endpoint", "http://127.0.0.1:8999", "Server endpoint")
	flag.Parse()

	args := flag.Args()
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "Expected command")
		os.Exit(1)
	}
	command := args[0]

	client := sketchlog.NewClient(sketchlog.ClientOptions{
		Endpoint:   *endpoint,
		MaxRetries: 3,
	})

	ctx := context.Background()

	switch command {
	case "test-ingest":
		batch := sketchlog.EventBatch{
			Latencies: []float64{42.5},
			Uniques:   []string{"user_1"},
			Events:    map[string]int64{"test_event": 1},
		}
		if err := client.IngestEvents(ctx, "test-stream", batch); err != nil {
			fmt.Fprintf(os.Stderr, "Ingest error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Ingest success")

	case "test-retries":
		if err := client.TestFlake(ctx); err != nil {
			fmt.Fprintf(os.Stderr, "Retry error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Retry/Health success")

	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", command)
		os.Exit(1)
	}
}
