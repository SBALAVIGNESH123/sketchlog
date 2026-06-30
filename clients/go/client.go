package sketchlog

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Client struct {
	endpoint           string
	maxRetries         int
	httpClient         *http.Client
	authToken          string
	authTokenProvider  func(context.Context) (string, error)
	configurationError string
}

const maxResponseBytes int64 = 1024 * 1024

func NewClient(opts ClientOptions) *Client {
	if opts.MaxRetries == 0 {
		opts.MaxRetries = 3
	}

	if opts.Timeout == 0 {
		opts.Timeout = 10 * time.Second
	}

	transport := &http.Transport{
		MaxIdleConns:        100,
		MaxIdleConnsPerHost: 100,
		IdleConnTimeout:     90 * time.Second,
	}
	parsedEndpoint, parseErr := url.Parse(opts.Endpoint)
	configurationError := ""
	if parseErr != nil ||
		parsedEndpoint.Scheme != "http" && parsedEndpoint.Scheme != "https" ||
		parsedEndpoint.Hostname() == "" ||
		parsedEndpoint.User != nil ||
		parsedEndpoint.Path != "" && parsedEndpoint.Path != "/" ||
		parsedEndpoint.RawQuery != "" ||
		parsedEndpoint.Fragment != "" {
		configurationError = "Endpoint must be an HTTP(S) origin without credentials or a path"
	}
	if opts.MaxRetries < 0 || opts.MaxRetries > 10 {
		configurationError = "MaxRetries must be in [0, 10]"
	}
	if opts.Timeout < 0 {
		configurationError = "Timeout must not be negative"
	}

	return &Client{
		endpoint:           strings.TrimRight(opts.Endpoint, "/"),
		maxRetries:         opts.MaxRetries,
		authToken:          opts.AuthToken,
		authTokenProvider:  opts.AuthTokenProvider,
		configurationError: configurationError,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   opts.Timeout,
			CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}
}

func (c *Client) request(ctx context.Context, method, path string, body interface{}) ([]byte, error) {
	if c.configurationError != "" {
		return nil, &SketchLogError{
			StatusCode: http.StatusBadRequest,
			Message:    c.configurationError,
		}
	}
	url := c.endpoint + path

	var reqBody []byte
	var err error
	if body != nil {
		reqBody, err = json.Marshal(body)
		if err != nil {
			return nil, &SketchLogError{StatusCode: 400, Message: "Marshal Error: " + err.Error()}
		}
	}

	attempt := 0
	isIdempotent := (method == "GET" || method == "PUT" || method == "DELETE")

	for {
		attempt++

		var bodyReader io.Reader
		if body != nil {
			bodyReader = bytes.NewReader(reqBody)
		}

		req, err := http.NewRequestWithContext(ctx, method, url, bodyReader)
		if err != nil {
			return nil, &SketchLogError{StatusCode: 400, Message: "Marshal Error: " + err.Error()}
		}

		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		token := c.authToken
		if c.authTokenProvider != nil {
			token, err = c.authTokenProvider(ctx)
			if err != nil {
				return nil, &SketchLogError{StatusCode: 401, Message: "Credential provider failed"}
			}
		}
		if token != "" {
			req.Header.Set("X-SketchLog-Auth-Token", token)
		}

		res, err := c.httpClient.Do(req)

		if err != nil {
			if ctx.Err() != nil {
				return nil, err
			}
			if isIdempotent && attempt <= c.maxRetries {
				if err := c.delay(ctx, attempt); err != nil {
					return nil, err
				}
				continue
			}
			return nil, &SketchLogError{StatusCode: 503, Message: "Transport Error: " + err.Error()}
		}

		resBody, readErr := io.ReadAll(io.LimitReader(
			res.Body, maxResponseBytes+1))
		closeErr := res.Body.Close()
		if readErr != nil {
			return nil, &SketchLogError{
				StatusCode: 502,
				Message:    "Failed to read server response",
			}
		}
		if closeErr != nil {
			return nil, &SketchLogError{
				StatusCode: 502,
				Message:    "Failed to close server response",
			}
		}
		if int64(len(resBody)) > maxResponseBytes {
			return nil, &SketchLogError{
				StatusCode: 502,
				Message:    "Server response exceeds 1 MiB",
			}
		}

		if res.StatusCode >= 200 && res.StatusCode < 300 {
			return resBody, nil
		}

		if (res.StatusCode >= 500 || res.StatusCode == 429) && isIdempotent && attempt <= c.maxRetries {
			if err := c.delay(ctx, attempt); err != nil {
				return nil, err
			}
			continue
		}

		return nil, &SketchLogError{
			StatusCode: res.StatusCode,
			Message:    fmt.Sprintf("HTTP %d: %s", res.StatusCode, string(resBody)),
		}
	}
}

func (c *Client) delay(ctx context.Context, attempt int) error {
	base := 100.0 * math.Pow(2, float64(attempt-1))
	rng := rand.New(rand.NewSource(time.Now().UnixNano()))
	jitter := rng.Float64() * 50.0
	delay := time.Duration(base+jitter) * time.Millisecond

	timer := time.NewTimer(delay)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (c *Client) Health(ctx context.Context) error {
	_, err := c.request(ctx, "GET", "/health", nil)
	return err
}

func (c *Client) IngestEvents(ctx context.Context, streamID string, batch EventBatch) error {
	escapedID := url.PathEscape(streamID)
	_, err := c.request(ctx, "POST", "/v1/streams/"+escapedID+"/events", batch)
	return err
}

func (c *Client) Close() {
	c.httpClient.CloseIdleConnections()
}
