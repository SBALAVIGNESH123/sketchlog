package sketch

import (
	"fmt"
	"math"
	"testing"
)

// ── DDSketch ──────────────────────────────────────────────────────────────────

func TestDDSketchInvalidAlpha(t *testing.T) {
	for _, a := range []float64{0, -0.1, 1.0, 1.5} {
		if _, err := NewDDSketch(a); err == nil {
			t.Errorf("expected error for alpha=%v", a)
		}
	}
}

func TestDDSketchEmptyQuantile(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	if _, err := s.Quantile(0.5); err == nil {
		t.Error("expected error on empty sketch")
	}
}

func TestDDSketchInvalidQuantile(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	s.Add(1)
	if _, err := s.Quantile(-0.1); err == nil {
		t.Error("expected error for q=-0.1")
	}
	if _, err := s.Quantile(1.1); err == nil {
		t.Error("expected error for q=1.1")
	}
}

func TestDDSketchAccuracy(t *testing.T) {
	alpha := 0.01
	s, _ := NewDDSketch(alpha)
	n := 10000
	for i := 1; i <= n; i++ {
		s.Add(float64(i))
	}
	if s.Count() != uint64(n) {
		t.Fatalf("count: got %d, want %d", s.Count(), n)
	}
	for _, q := range []float64{0.5, 0.9, 0.99} {
		got, err := s.Quantile(q)
		if err != nil {
			t.Fatal(err)
		}
		expected := q * float64(n)
		relErr := math.Abs(got-expected) / expected
		if relErr > alpha+0.005 {
			t.Errorf("q=%.2f: got=%.2f expected=%.2f relErr=%.4f", q, got, expected, relErr)
		}
	}
}

func TestDDSketchZeroValues(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	for i := 0; i < 5; i++ {
		s.Add(0)
	}
	s.Add(1)
	if s.Count() != 6 {
		t.Fatalf("count: got %d, want 6", s.Count())
	}
	v, err := s.Quantile(0.5)
	if err != nil {
		t.Fatal(err)
	}
	if v != 0 {
		t.Errorf("expected 0 at p50, got %v", v)
	}
}

func TestDDSketchMerge(t *testing.T) {
	a, _ := NewDDSketch(0.01)
	b, _ := NewDDSketch(0.01)
	for i := 1; i <= 500; i++ {
		a.Add(float64(i))
	}
	for i := 501; i <= 1000; i++ {
		b.Add(float64(i))
	}
	if err := a.Merge(b); err != nil {
		t.Fatal(err)
	}
	if a.Count() != 1000 {
		t.Fatalf("count: got %d, want 1000", a.Count())
	}
	v, err := a.Quantile(0.5)
	if err != nil {
		t.Fatal(err)
	}
	if v < 490 || v > 510 {
		t.Errorf("p50 out of range: %v", v)
	}
}

func TestDDSketchMergeMismatch(t *testing.T) {
	a, _ := NewDDSketch(0.01)
	b, _ := NewDDSketch(0.02)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different alpha")
	}
}

func TestDDSketchNegativeIgnored(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	s.Add(-5)
	if s.Count() != 0 {
		t.Error("negative value should be ignored")
	}
}

// ── HyperLogLog ───────────────────────────────────────────────────────────────

func TestHLLInvalidPrecision(t *testing.T) {
	for _, p := range []uint8{0, 3, 19} {
		if _, err := NewHyperLogLog(p); err == nil {
			t.Errorf("expected error for precision=%d", p)
		}
	}
}

func TestHLLCardinality(t *testing.T) {
	hll, _ := NewHyperLogLog(14)
	n := 100000
	for i := 0; i < n; i++ {
		hll.Add([]byte(fmt.Sprintf("item-%d", i)))
	}
	got := hll.Count()
	errPct := math.Abs(float64(got)-float64(n)) / float64(n)
	if errPct > 0.02 {
		t.Errorf("cardinality error %.2f%% > 2%% (got %d, want %d)", errPct*100, got, n)
	}
}

func TestHLLDuplicates(t *testing.T) {
	hll, _ := NewHyperLogLog(14)
	for i := 0; i < 1000; i++ {
		hll.Add([]byte("same"))
	}
	got := hll.Count()
	if got > 5 {
		t.Errorf("expected ~1 for duplicates, got %d", got)
	}
}

func TestHLLMerge(t *testing.T) {
	a, _ := NewHyperLogLog(14)
	b, _ := NewHyperLogLog(14)
	for i := 0; i < 50000; i++ {
		a.Add([]byte(fmt.Sprintf("a-%d", i)))
		b.Add([]byte(fmt.Sprintf("b-%d", i)))
	}
	if err := a.Merge(b); err != nil {
		t.Fatal(err)
	}
	got := a.Count()
	errPct := math.Abs(float64(got)-100000) / 100000
	if errPct > 0.03 {
		t.Errorf("merged cardinality error %.2f%% > 3%%", errPct*100)
	}
}

func TestHLLMergeMismatch(t *testing.T) {
	a, _ := NewHyperLogLog(10)
	b, _ := NewHyperLogLog(12)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different precision")
	}
}

// ── CountMinSketch ────────────────────────────────────────────────────────────

func TestCMSInvalidParams(t *testing.T) {
	for _, tc := range []struct{ e, d float64 }{
		{0, 0.1}, {1, 0.1}, {0.1, 0}, {0.1, 1},
	} {
		if _, err := NewCountMinSketch(tc.e, tc.d); err == nil {
			t.Errorf("expected error for epsilon=%v delta=%v", tc.e, tc.d)
		}
	}
}

func TestCMSInvalidDimensions(t *testing.T) {
	if _, err := NewCountMinSketchFromDimensions(0, 5); err == nil {
		t.Error("expected error for width=0")
	}
	if _, err := NewCountMinSketchFromDimensions(5, 0); err == nil {
		t.Error("expected error for depth=0")
	}
}

func TestCMSOverflowGuard(t *testing.T) {
	// epsilon=1e-11 would overflow uint32 without the guard
	if _, err := NewCountMinSketch(1e-11, 0.01); err == nil {
		t.Error("expected error for very small epsilon")
	}
}

func TestCMSFrequency(t *testing.T) {
	cms, _ := NewCountMinSketch(0.001, 0.01)
	items := []string{"apple", "banana", "cherry"}
	counts := map[string]uint64{"apple": 100, "banana": 50, "cherry": 25}
	for _, item := range items {
		for i := uint64(0); i < counts[item]; i++ {
			cms.Add([]byte(item), 1)
		}
	}
	for _, item := range items {
		got := cms.Count([]byte(item))
		want := counts[item]
		if got < want {
			t.Errorf("%s: got %d < want %d", item, got, want)
		}
	}
}

func TestCMSTotalCount(t *testing.T) {
	cms, _ := NewCountMinSketch(0.01, 0.01)
	cms.Add([]byte("x"), 5)
	cms.Add([]byte("y"), 3)
	if cms.TotalCount() != 8 {
		t.Errorf("total: got %d, want 8", cms.TotalCount())
	}
}

func TestCMSMerge(t *testing.T) {
	a, _ := NewCountMinSketchFromDimensions(100, 5)
	b, _ := NewCountMinSketchFromDimensions(100, 5)
	a.Add([]byte("foo"), 10)
	b.Add([]byte("foo"), 20)
	if err := a.Merge(b); err != nil {
		t.Fatal(err)
	}
	if got := a.Count([]byte("foo")); got < 30 {
		t.Errorf("expected >= 30 after merge, got %d", got)
	}
}

func TestCMSMergeMismatch(t *testing.T) {
	a, _ := NewCountMinSketchFromDimensions(100, 5)
	b, _ := NewCountMinSketchFromDimensions(200, 5)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different dimensions")
	}
}
