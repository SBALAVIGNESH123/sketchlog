// Package sketch provides native Go implementations of probabilistic data
// structures for embedded use - no network connection required.
package sketch

import (
	"errors"
	"math"
)

// DDSketch estimates quantiles with a guaranteed relative-error bound alpha.
// For a value v the reported quantile estimate q satisfies:
//
//	|q - v| / v <= alpha
type DDSketch struct {
	alpha   float64
	gamma   float64
	buckets map[int]uint64
	count   float64
	sum     float64
	zeros   uint64
	minVal  float64
	maxVal  float64
}

// NewDDSketch creates a DDSketch with relative accuracy alpha (0 < alpha < 1).
func NewDDSketch(alpha float64) (*DDSketch, error) {
	if alpha <= 0 || alpha >= 1 {
		return nil, errors.New("ddsketch: alpha must be in (0, 1)")
	}
	return &DDSketch{
		alpha:   alpha,
		gamma:   (1 + alpha) / (1 - alpha),
		buckets: make(map[int]uint64),
		minVal:  math.MaxFloat64,
		maxVal:  -math.MaxFloat64,
	}, nil
}

func (s *DDSketch) bucketIndex(v float64) int {
	return int(math.Ceil(math.Log(v) / math.Log(s.gamma)))
}

// Add inserts a non-negative value into the sketch.
func (s *DDSketch) Add(v float64) error {
	if v < 0 {
		return errors.New("ddsketch: negative values are not supported")
	}
	s.count++
	s.sum += v
	if v < s.minVal {
		s.minVal = v
	}
	if v > s.maxVal {
		s.maxVal = v
	}
	if v == 0 {
		s.zeros++
		return nil
	}
	idx := s.bucketIndex(v)
	s.buckets[idx]++
	return nil
}

// Quantile returns the q-th quantile estimate (0 <= q <= 1).
func (s *DDSketch) Quantile(q float64) (float64, error) {
	if s.count == 0 {
		return 0, errors.New("ddsketch: sketch is empty")
	}
	if q < 0 || q > 1 {
		return 0, errors.New("ddsketch: q must be in [0, 1]")
	}
	if q == 0 {
		return s.minVal, nil
	}
	if q == 1 {
		return s.maxVal, nil
	}
	target := q * s.count
	cumulative := float64(s.zeros)
	if cumulative >= target {
		return 0, nil
	}
	type kv struct {
		k int
		v uint64
	}
	pairs := make([]kv, 0, len(s.buckets))
	for k, v := range s.buckets {
		pairs = append(pairs, kv{k, v})
	}
	// sort by bucket index
	for i := 1; i < len(pairs); i++ {
		for j := i; j > 0 && pairs[j-1].k > pairs[j].k; j-- {
			pairs[j-1], pairs[j] = pairs[j], pairs[j-1]
		}
	}
	for _, p := range pairs {
		cumulative += float64(p.v)
		if cumulative >= target {
			return 2 * math.Pow(s.gamma, float64(p.k)) / (s.gamma + 1), nil
		}
	}
	return s.maxVal, nil
}

// Count returns the number of values added.
func (s *DDSketch) Count() float64 { return s.count }

// Sum returns the sum of all added values.
func (s *DDSketch) Sum() float64 { return s.sum }

// Merge merges other into s. Both must have the same alpha.
func (s *DDSketch) Merge(other *DDSketch) error {
	if s.alpha != other.alpha {
		return errors.New("ddsketch: cannot merge sketches with different alpha")
	}
	s.count += other.count
	s.sum += other.sum
	s.zeros += other.zeros
	for k, v := range other.buckets {
		s.buckets[k] += v
	}
	if other.minVal < s.minVal {
		s.minVal = other.minVal
	}
	if other.maxVal > s.maxVal {
		s.maxVal = other.maxVal
	}
	return nil
}
