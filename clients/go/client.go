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
	endpoint   string
	maxRetries int
	httpClient *http.Client
}

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

	return &Client{
		endpoint:   strings.TrimRight(opts.Endpoint, "/"),
		maxRetries: opts.MaxRetries,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   opts.Timeout,
		},
	}
}

func (c *Client) request(ctx context.Context, method, path string, body interface{}) ([]byte, error) {
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

		resBody, _ := io.ReadAll(res.Body)
		res.Body.Close()

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
			Message:    fmt.Sprintf("Req: %s | Res: %s", string(reqBody), string(resBody)),
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

// TestFlake is an internal method used by the conformance suite to verify retry logic.
func (c *Client) TestFlake(ctx context.Context) error {
	_, err := c.request(ctx, "GET", "/test/flake", nil)
	return err
}
