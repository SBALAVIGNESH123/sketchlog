package sketch

import (
	"fmt"
	"math"
	"testing"
)

// ── DDSketch ────────────────────────────────────────────────────────────────

func TestDDSketch_InvalidAlpha(t *testing.T) {
	for _, a := range []float64{-1, 0, 1, 2} {
		if _, err := NewDDSketch(a); err == nil {
			t.Errorf("expected error for alpha=%v", a)
		}
	}
}

func TestDDSketch_EmptyQuantile(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	if _, err := s.Quantile(0.5); err == nil {
		t.Error("expected error on empty sketch")
	}
}

func TestDDSketch_InvalidQuantile(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	_ = s.Add(1.0)
	if _, err := s.Quantile(-0.1); err == nil {
		t.Error("expected error for q < 0")
	}
	if _, err := s.Quantile(1.1); err == nil {
		t.Error("expected error for q > 1")
	}
}

func TestDDSketch_NegativeValue(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	if err := s.Add(-1.0); err == nil {
		t.Error("expected error for negative value")
	}
}

func TestDDSketch_ZeroValue(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	if err := s.Add(0.0); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if s.Count() != 1 {
		t.Errorf("expected count 1, got %v", s.Count())
	}
	v, err := s.Quantile(0.5)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if v != 0 {
		t.Errorf("expected 0, got %v", v)
	}
}

func TestDDSketch_RelativeAccuracy(t *testing.T) {
	alpha := 0.01
	s, _ := NewDDSketch(alpha)
	n := 10000
	for i := 1; i <= n; i++ {
		if err := s.Add(float64(i)); err != nil {
			t.Fatalf("Add failed: %v", err)
		}
	}
	if s.Count() != float64(n) {
		t.Errorf("expected count %d, got %v", n, s.Count())
	}
	for _, q := range []float64{0.5, 0.9, 0.99} {
		got, err := s.Quantile(q)
		if err != nil {
			t.Fatalf("Quantile(%v) error: %v", q, err)
		}
		expected := q * float64(n)
		relErr := math.Abs(got-expected) / expected
		if relErr > 2*alpha+0.01 {
			t.Errorf("q=%v: got %v, expected ~%v, relErr=%v", q, got, expected, relErr)
		}
	}
}

func TestDDSketch_MinMax(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	for _, v := range []float64{5, 1, 10, 3} {
		_ = s.Add(v)
	}
	min, _ := s.Quantile(0)
	max, _ := s.Quantile(1)
	if min != 1 {
		t.Errorf("expected min=1, got %v", min)
	}
	if max != 10 {
		t.Errorf("expected max=10, got %v", max)
	}
}

func TestDDSketch_Sum(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	for _, v := range []float64{1, 2, 3, 4} {
		_ = s.Add(v)
	}
	if s.Sum() != 10 {
		t.Errorf("expected sum=10, got %v", s.Sum())
	}
}

func TestDDSketch_Merge(t *testing.T) {
	a, _ := NewDDSketch(0.01)
	b, _ := NewDDSketch(0.01)
	for i := 1; i <= 500; i++ {
		_ = a.Add(float64(i))
	}
	for i := 501; i <= 1000; i++ {
		_ = b.Add(float64(i))
	}
	if err := a.Merge(b); err != nil {
		t.Fatalf("Merge failed: %v", err)
	}
	if a.Count() != 1000 {
		t.Errorf("expected count 1000, got %v", a.Count())
	}
	p50, _ := a.Quantile(0.5)
	if p50 < 400 || p50 > 600 {
		t.Errorf("p50 out of range: %v", p50)
	}
}

func TestDDSketch_MergeIncompatible(t *testing.T) {
	a, _ := NewDDSketch(0.01)
	b, _ := NewDDSketch(0.02)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different alpha")
	}
}

// ── HyperLogLog ─────────────────────────────────────────────────────────────

func TestHLL_InvalidPrecision(t *testing.T) {
	for _, p := range []uint8{0, 3, 19} {
		if _, err := NewHyperLogLog(p); err == nil {
			t.Errorf("expected error for precision=%d", p)
		}
	}
}

func TestHLL_Cardinality(t *testing.T) {
	h, _ := NewHyperLogLog(14)
	n := 10000
	for i := 0; i < n; i++ {
		h.Add([]byte(fmt.Sprintf("item-%d", i)))
	}
	got := h.Count()
	expected := uint64(n)
	// Allow 5% error for p=14 (~0.81% std error)
	lo := uint64(float64(expected) * 0.90)
	hi := uint64(float64(expected) * 1.10)
	if got < lo || got > hi {
		t.Errorf("cardinality estimate %d outside [%d, %d] for n=%d", got, lo, hi, n)
	}
}

func TestHLL_Duplicates(t *testing.T) {
	h, _ := NewHyperLogLog(14)
	for i := 0; i < 1000; i++ {
		h.Add([]byte("same-item"))
	}
	got := h.Count()
	if got > 5 {
		t.Errorf("expected ~1 distinct, got %d", got)
	}
}

func TestHLL_Merge(t *testing.T) {
	a, _ := NewHyperLogLog(14)
	b, _ := NewHyperLogLog(14)
	for i := 0; i < 5000; i++ {
		a.Add([]byte(fmt.Sprintf("a-%d", i)))
		b.Add([]byte(fmt.Sprintf("b-%d", i)))
	}
	if err := a.Merge(b); err != nil {
		t.Fatalf("Merge failed: %v", err)
	}
	got := a.Count()
	lo := uint64(7000)
	hi := uint64(13000)
	if got < lo || got > hi {
		t.Errorf("merged cardinality %d outside [%d, %d]", got, lo, hi)
	}
}

func TestHLL_MergeIncompatible(t *testing.T) {
	a, _ := NewHyperLogLog(10)
	b, _ := NewHyperLogLog(14)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different precision")
	}
}

// ── CountMinSketch ──────────────────────────────────────────────────────────

func TestCMS_InvalidParams(t *testing.T) {
	if _, err := NewCountMinSketch(0, 0.01); err == nil {
		t.Error("expected error for epsilon=0")
	}
	if _, err := NewCountMinSketch(1, 0.01); err == nil {
		t.Error("expected error for epsilon=1")
	}
	if _, err := NewCountMinSketch(0.01, 0); err == nil {
		t.Error("expected error for delta=0")
	}
	if _, err := NewCountMinSketch(0.01, 1); err == nil {
		t.Error("expected error for delta=1")
	}
}

func TestCMS_InvalidDimensions(t *testing.T) {
	if _, err := NewCountMinSketchFromDimensions(0, 5); err == nil {
		t.Error("expected error for width=0")
	}
	if _, err := NewCountMinSketchFromDimensions(100, 0); err == nil {
		t.Error("expected error for depth=0")
	}
}

func TestCMS_Frequency(t *testing.T) {
	c, _ := NewCountMinSketchFromDimensions(1000, 7)
	items := []struct {
		key   string
		count uint64
	}{
		{"apple", 100},
		{"banana", 50},
		{"cherry", 200},
	}
	for _, item := range items {
		for i := uint64(0); i < item.count; i++ {
			c.Add([]byte(item.key), 1)
		}
	}
	for _, item := range items {
		got := c.Count([]byte(item.key))
		if got < item.count {
			t.Errorf("%s: got %d, expected ≥ %d", item.key, got, item.count)
		}
	}
}

func TestCMS_TotalCount(t *testing.T) {
	c, _ := NewCountMinSketchFromDimensions(100, 5)
	c.Add([]byte("a"), 10)
	c.Add([]byte("b"), 20)
	if c.TotalCount() != 30 {
		t.Errorf("expected total 30, got %d", c.TotalCount())
	}
}

func TestCMS_Dimensions(t *testing.T) {
	c, _ := NewCountMinSketchFromDimensions(500, 7)
	if c.Width() != 500 {
		t.Errorf("expected width 500, got %d", c.Width())
	}
	if c.Depth() != 7 {
		t.Errorf("expected depth 7, got %d", c.Depth())
	}
}

func TestCMS_Merge(t *testing.T) {
	a, _ := NewCountMinSketchFromDimensions(1000, 5)
	b, _ := NewCountMinSketchFromDimensions(1000, 5)
	a.Add([]byte("key"), 30)
	b.Add([]byte("key"), 20)
	if err := a.Merge(b); err != nil {
		t.Fatalf("Merge failed: %v", err)
	}
	if a.TotalCount() != 50 {
		t.Errorf("expected total 50, got %d", a.TotalCount())
	}
	got := a.Count([]byte("key"))
	if got < 50 {
		t.Errorf("expected ≥ 50, got %d", got)
	}
}

func TestCMS_MergeIncompatible(t *testing.T) {
	a, _ := NewCountMinSketchFromDimensions(100, 5)
	b, _ := NewCountMinSketchFromDimensions(200, 5)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different dimensions")
	}
}

func TestCMS_FromEpsilonDelta(t *testing.T) {
	c, err := NewCountMinSketch(0.01, 0.01)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if c.Width() == 0 || c.Depth() == 0 {
		t.Error("expected non-zero dimensions")
	}
}
