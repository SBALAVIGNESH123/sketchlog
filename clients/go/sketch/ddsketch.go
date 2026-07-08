package sketch

import (
	"errors"
	"math"
	"sort"
)

// DDSketch is a quantile sketch with a relative-error guarantee of alpha.
type DDSketch struct {
	alpha     float64
	gamma     float64
	logGamma  float64
	buckets   map[int]uint64
	zeroCount uint64
	count     uint64
	sum       float64
	minVal    float64
	maxVal    float64
}

// NewDDSketch creates a DDSketch with the given relative accuracy (0 < alpha < 1).
func NewDDSketch(alpha float64) (*DDSketch, error) {
	if alpha <= 0 || alpha >= 1 {
		return nil, errors.New("alpha must be in (0, 1)")
	}
	gamma := (1 + alpha) / (1 - alpha)
	return &DDSketch{
		alpha:    alpha,
		gamma:    gamma,
		logGamma: math.Log(gamma),
		buckets:  make(map[int]uint64),
		minVal:   math.Inf(1),
		maxVal:   math.Inf(-1),
	}, nil
}

func (d *DDSketch) bucketIndex(v float64) int {
	return int(math.Ceil(math.Log(v) / d.logGamma))
}

// Add inserts a value into the sketch.
func (d *DDSketch) Add(v float64) {
	if v < 0 {
		return
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
		return
	}
	d.buckets[d.bucketIndex(v)]++
}

// Count returns the total number of values added.
func (d *DDSketch) Count() uint64 { return d.count }

// Sum returns the sum of all values added.
func (d *DDSketch) Sum() float64 { return d.sum }

// Quantile returns the estimated q-th quantile (0 <= q <= 1).
func (d *DDSketch) Quantile(q float64) (float64, error) {
	if q < 0 || q > 1 {
		return 0, errors.New("q must be in [0, 1]")
	}
	if d.count == 0 {
		return 0, errors.New("sketch is empty")
	}
	rank := uint64(math.Ceil(q * float64(d.count)))
	// zeroes
	if rank <= d.zeroCount {
		return 0, nil
	}
	rank -= d.zeroCount
	keys := make([]int, 0, len(d.buckets))
	for k := range d.buckets {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	var cum uint64
	for _, k := range keys {
		cum += d.buckets[k]
		if cum >= rank {
			return 2 * math.Pow(d.gamma, float64(k)) / (d.gamma + 1), nil
		}
	}
	return d.maxVal, nil
}

// Merge combines another DDSketch into this one (same alpha required).
func (d *DDSketch) Merge(other *DDSketch) error {
	if d.alpha != other.alpha {
		return errors.New("alpha mismatch")
	}
	for k, v := range other.buckets {
		d.buckets[k] += v
	}
	d.zeroCount += other.zeroCount
	d.count += other.count
	d.sum += other.sum
	if other.minVal < d.minVal {
		d.minVal = other.minVal
	}
	if other.maxVal > d.maxVal {
		d.maxVal = other.maxVal
	}
	return nil
}
