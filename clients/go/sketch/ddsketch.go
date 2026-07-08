// Package sketch provides native Go implementations of probabilistic data
// structures for embedded use — no network connection required.
package sketch

import (
	"errors"
	"math"
	"sort"
)

// DDSketch is a quantile sketch with relative-error guarantees.
// For any quantile q, the returned value v satisfies:
//
//	|v_true - v| / v_true ≤ alpha
//
// Reference: "DDSketch: A fast and fully-mergeable quantile sketch with
// relative-error guarantees" (Masson et al., 2019).
type DDSketch struct {
	alpha     float64
	gamma     float64 // 1 + 2*alpha/(1-alpha)
	gammaLn   float64 // ln(gamma)
	buckets   map[int]float64
	count     float64
	sum       float64
	minVal    float64
	maxVal    float64
	zeroCount float64
}

// NewDDSketch creates a DDSketch with relative accuracy alpha (0 < alpha < 1).
// A typical value is 0.01 (1% relative error).
func NewDDSketch(alpha float64) (*DDSketch, error) {
	if alpha <= 0 || alpha >= 1 {
		return nil, errors.New("ddsketch: alpha must be in (0, 1)")
	}
	gamma := 1.0 + 2.0*alpha/(1.0-alpha)
	return &DDSketch{
		alpha:   alpha,
		gamma:   gamma,
		gammaLn: math.Log(gamma),
		buckets: make(map[int]float64),
		minVal:  math.Inf(1),
		maxVal:  math.Inf(-1),
	}, nil
}

func (d *DDSketch) bucketIndex(v float64) int {
	return int(math.Ceil(math.Log(v) / d.gammaLn))
}

// Add inserts a non-negative value into the sketch.
func (d *DDSketch) Add(v float64) error {
	if v < 0 {
		return errors.New("ddsketch: negative values not supported")
	}
	d.count++
	d.sum += v
	if v < d.minVal {
		d.minVal = v
	}
	if v > d.maxVal {
		d.maxVal = v
	}
	if v == 0 {
		d.zeroCount++
		return nil
	}
	d.buckets[d.bucketIndex(v)]++
	return nil
}

// Count returns the total number of values added.
func (d *DDSketch) Count() float64 { return d.count }

// Sum returns the sum of all values added.
func (d *DDSketch) Sum() float64 { return d.sum }

// Quantile returns the approximate value at quantile q (0 ≤ q ≤ 1).
func (d *DDSketch) Quantile(q float64) (float64, error) {
	if q < 0 || q > 1 {
		return 0, errors.New("ddsketch: q must be in [0, 1]")
	}
	if d.count == 0 {
		return 0, errors.New("ddsketch: sketch is empty")
	}
	if q == 0 {
		return d.minVal, nil
	}
	if q == 1 {
		return d.maxVal, nil
	}

	rank := q * d.count
	if rank <= d.zeroCount {
		return 0, nil
	}
	rank -= d.zeroCount

	keys := make([]int, 0, len(d.buckets))
	for k := range d.buckets {
		keys = append(keys, k)
	}
	sort.Ints(keys)

	cumulative := 0.0
	for _, k := range keys {
		cumulative += d.buckets[k]
		if cumulative >= rank {
			return 2.0 * math.Pow(d.gamma, float64(k)) / (1.0 + d.gamma), nil
		}
	}
	return d.maxVal, nil
}

// Merge merges other into d. Both must have the same alpha.
func (d *DDSketch) Merge(other *DDSketch) error {
	if d.alpha != other.alpha {
		return errors.New("ddsketch: cannot merge sketches with different alpha")
	}
	d.count += other.count
	d.sum += other.sum
	d.zeroCount += other.zeroCount
	if other.minVal < d.minVal {
		d.minVal = other.minVal
	}
	if other.maxVal > d.maxVal {
		d.maxVal = other.maxVal
	}
	for k, v := range other.buckets {
		d.buckets[k] += v
	}
	return nil
}
