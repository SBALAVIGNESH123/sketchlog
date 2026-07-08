package sketch

import (
	"fmt"
	"math"
	"testing"
)

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
	for _, q := range []float64{-0.1, 1.1} {
		if _, err := s.Quantile(q); err == nil {
			t.Errorf("expected error for q=%v", q)
		}
	}
}

func TestDDSketch_NegativeValue(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	if err := s.Add(-1.0); err == nil {
		t.Error("expected error for negative value")
	}
}

func TestDDSketch_Accuracy(t *testing.T) {
	alpha := 0.01
	s, _ := NewDDSketch(alpha)
	n := 1000
	for i := 1; i <= n; i++ {
		_ = s.Add(float64(i))
	}
	if s.Count() != float64(n) {
		t.Errorf("count: got %v, want %v", s.Count(), n)
	}
	for _, q := range []float64{0.5, 0.9, 0.99} {
		v, err := s.Quantile(q)
		if err != nil {
			t.Fatalf("Quantile(%v) error: %v", q, err)
		}
		expected := q * float64(n)
		relErr := math.Abs(v-expected) / expected
		if relErr > alpha+0.01 {
			t.Errorf("q=%v: got %v want ~%v relErr=%v", q, v, expected, relErr)
		}
	}
}

func TestDDSketch_Zero(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	_ = s.Add(0)
	_ = s.Add(1)
	v, err := s.Quantile(0)
	if err != nil {
		t.Fatal(err)
	}
	if v != 0 {
		t.Errorf("p0 of [0,1]: got %v want 0", v)
	}
}

func TestDDSketch_Merge(t *testing.T) {
	a, _ := NewDDSketch(0.01)
	b, _ := NewDDSketch(0.01)
	for i := 1; i <= 100; i++ {
		_ = a.Add(float64(i))
	}
	for i := 101; i <= 200; i++ {
		_ = b.Add(float64(i))
	}
	if err := a.Merge(b); err != nil {
		t.Fatal(err)
	}
	if a.Count() != 200 {
		t.Errorf("merged count: got %v want 200", a.Count())
	}
}

func TestDDSketch_MergeDifferentAlpha(t *testing.T) {
	a, _ := NewDDSketch(0.01)
	b, _ := NewDDSketch(0.02)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different alpha")
	}
}

func TestDDSketch_Sum(t *testing.T) {
	s, _ := NewDDSketch(0.01)
	for i := 1; i <= 10; i++ {
		_ = s.Add(float64(i))
	}
	if s.Sum() != 55 {
		t.Errorf("sum: got %v want 55", s.Sum())
	}
}

func TestHLL_InvalidPrecision(t *testing.T) {
	for _, p := range []uint8{0, 3, 19} {
		if _, err := NewHyperLogLog(p); err == nil {
			t.Errorf("expected error for precision=%v", p)
		}
	}
}

func TestHLL_Empty(t *testing.T) {
	h, _ := NewHyperLogLog(14)
	if h.Count() != 0 {
		t.Errorf("empty HLL count: got %v want 0", h.Count())
	}
}

func TestHLL_Cardinality(t *testing.T) {
	h, _ := NewHyperLogLog(14)
	n := 10000
	for i := 0; i < n; i++ {
		h.Add([]byte(fmt.Sprintf("item-%d", i)))
	}
	est := h.Count()
	errPct := math.Abs(float64(est)-float64(n)) / float64(n) * 100
	if errPct > 5 {
		t.Errorf("HLL error %.2f%% > 5%% (est=%d n=%d)", errPct, est, n)
	}
}

func TestHLL_Duplicates(t *testing.T) {
	h, _ := NewHyperLogLog(14)
	for i := 0; i < 1000; i++ {
		h.Add([]byte("same"))
	}
	est := h.Count()
	if est > 5 {
		t.Errorf("duplicate HLL: got %d want ~1", est)
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
		t.Fatal(err)
	}
	est := a.Count()
	errPct := math.Abs(float64(est)-10000) / 10000 * 100
	if errPct > 5 {
		t.Errorf("merged HLL error %.2f%% > 5%%", errPct)
	}
}

func TestHLL_MergeDifferentPrecision(t *testing.T) {
	a, _ := NewHyperLogLog(14)
	b, _ := NewHyperLogLog(10)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different precision")
	}
}

func TestCMS_InvalidParams(t *testing.T) {
	for _, tc := range []struct{ e, d float64 }{
		{-1, 0.01}, {0, 0.01}, {1, 0.01}, {2, 0.01},
		{0.01, -1}, {0.01, 0}, {0.01, 1}, {0.01, 2},
	} {
		if _, err := NewCountMinSketch(tc.e, tc.d); err == nil {
			t.Errorf("expected error for epsilon=%v delta=%v", tc.e, tc.d)
		}
	}
}

func TestCMS_InvalidDimensions(t *testing.T) {
	if _, err := NewCountMinSketchFromDimensions(0, 5); err == nil {
		t.Error("expected error for width=0")
	}
	if _, err := NewCountMinSketchFromDimensions(5, 0); err == nil {
		t.Error("expected error for depth=0")
	}
}

func TestCMS_Count(t *testing.T) {
	c, _ := NewCountMinSketch(0.01, 0.01)
	key := []byte("hello")
	c.Add(key, 5)
	c.Add(key, 3)
	if got := c.Count(key); got < 8 {
		t.Errorf("Count: got %d want >= 8", got)
	}
}

func TestCMS_TotalCount(t *testing.T) {
	c, _ := NewCountMinSketchFromDimensions(100, 5)
	c.Add([]byte("a"), 3)
	c.Add([]byte("b"), 7)
	if c.TotalCount() != 10 {
		t.Errorf("TotalCount: got %d want 10", c.TotalCount())
	}
}

func TestCMS_Merge(t *testing.T) {
	a, _ := NewCountMinSketchFromDimensions(100, 5)
	b, _ := NewCountMinSketchFromDimensions(100, 5)
	key := []byte("x")
	a.Add(key, 4)
	b.Add(key, 6)
	if err := a.Merge(b); err != nil {
		t.Fatal(err)
	}
	if got := a.Count(key); got < 10 {
		t.Errorf("merged Count: got %d want >= 10", got)
	}
}

func TestCMS_MergeDifferentDimensions(t *testing.T) {
	a, _ := NewCountMinSketchFromDimensions(100, 5)
	b, _ := NewCountMinSketchFromDimensions(200, 5)
	if err := a.Merge(b); err == nil {
		t.Error("expected error merging different dimensions")
	}
}

func TestCMS_Accuracy(t *testing.T) {
	c, _ := NewCountMinSketch(0.01, 0.001)
	n := 1000
	for i := 0; i < n; i++ {
		c.Add([]byte(fmt.Sprintf("item-%d", i%100)), 1)
	}
	key := []byte("item-0")
	if got := c.Count(key); got < 10 {
		t.Errorf("accuracy: got %d for item-0 want >= 10", got)
	}
}
