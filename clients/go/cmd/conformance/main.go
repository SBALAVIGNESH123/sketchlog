package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"

	sketchlog "github.com/SBALAVIGNESH123/sketchlog/clients/go"
)

func main() {
	endpoint := flag.String("endpoint", "http://127.0.0.1:8999", "Server endpoint")
	token := flag.String("token", "", "Server authentication token")
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
		AuthToken:  *token,
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
		if err := client.Health(ctx); err != nil {
			fmt.Fprintf(os.Stderr, "Retry error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Retry/Health success")

	case "test-transport-retries":
		if err := client.Health(ctx); err == nil {
			fmt.Fprintln(os.Stderr, "Expected transport failure")
			os.Exit(1)
		}
		fmt.Println("Transport retries success")

	case "test-auth-missing", "test-auth-invalid":
		authToken := ""
		if command == "test-auth-invalid" {
			authToken = "wrong-token"
		}
		unauthorized := sketchlog.NewClient(sketchlog.ClientOptions{
			Endpoint:  *endpoint,
			AuthToken: authToken,
		})
		err := unauthorized.IngestEvents(
			ctx, "auth-test", sketchlog.EventBatch{Latencies: []float64{1}})
		var apiError *sketchlog.SketchLogError
		if !errors.As(err, &apiError) || apiError.StatusCode != 401 {
			fmt.Fprintf(os.Stderr, "Expected HTTP 401, got %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Authentication rejection success")

	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", command)
		os.Exit(1)
	}
}
