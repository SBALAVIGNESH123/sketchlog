package sketchlog

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestClientDoesNotFollowRedirects(t *testing.T) {
	var redirected atomic.Bool
	target := httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			redirected.Store(true)
			w.WriteHeader(http.StatusOK)
		},
	))
	defer target.Close()

	source := httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, request *http.Request) {
			http.Redirect(
				w, request, target.URL, http.StatusTemporaryRedirect)
		},
	))
	defer source.Close()

	err := NewClient(ClientOptions{Endpoint: source.URL}).Health(
		context.Background())
	var apiError *SketchLogError
	if !errors.As(err, &apiError) || apiError.StatusCode != http.StatusTemporaryRedirect {
		t.Fatalf("expected redirect response, got %v", err)
	}
	if redirected.Load() {
		t.Fatal("client followed a redirect")
	}
}

func TestClientRejectsUnsafeEndpoints(t *testing.T) {
	for _, endpoint := range []string{
		"file:///tmp/state",
		"https://user:password@example.com",
		"https://example.com/base",
		"https://example.com?token=secret",
	} {
		client := NewClient(ClientOptions{Endpoint: endpoint})
		err := client.Health(context.Background())
		var apiError *SketchLogError
		if !errors.As(err, &apiError) ||
			apiError.StatusCode != http.StatusBadRequest {
			t.Fatalf("expected endpoint rejection for %q, got %v", endpoint, err)
		}
		client.Close()
	}
}

func TestErrorDoesNotContainRequestBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			http.Error(w, "invalid request", http.StatusBadRequest)
		},
	))
	defer server.Close()

	err := NewClient(ClientOptions{Endpoint: server.URL}).IngestEvents(
		context.Background(),
		"stream",
		EventBatch{Uniques: []string{"private-user-id"}},
	)
	if err == nil {
		t.Fatal("expected request failure")
	}
	if strings.Contains(err.Error(), "private-user-id") {
		t.Fatal("error exposed the request body")
	}
}

func TestResponseBodyLimit(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(strings.Repeat("x", int(maxResponseBytes)+1)))
		},
	))
	defer server.Close()

	err := NewClient(ClientOptions{Endpoint: server.URL}).Health(
		context.Background())
	var apiError *SketchLogError
	if !errors.As(err, &apiError) || apiError.StatusCode != http.StatusBadGateway {
		t.Fatalf("expected bounded-response error, got %v", err)
	}
}
