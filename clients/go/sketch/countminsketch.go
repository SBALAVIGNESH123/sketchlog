// Package sketch provides native Go implementations of probabilistic data
// structures for embedded use - no network connection required.
package sketch

import (
	"errors"
	"math"
)

// CountMinSketch estimates the frequency of elements in a data stream using
// O(width * depth) counters.
type CountMinSketch struct {
	width uint32
	depth uint32
	table [][]uint64
	count uint64
}

// NewCountMinSketch creates a CountMinSketch with error rate epsilon and
// failure probability delta (both in (0, 1)).
func NewCountMinSketch(epsilon, delta float64) (*CountMinSketch, error) {
	if epsilon <= 0 || epsilon >= 1 {
		return nil, errors.New("countminsketch: epsilon must be in (0, 1)")
	}
	if delta <= 0 || delta >= 1 {
		return nil, errors.New("countminsketch: delta must be in (0, 1)")
	}
	wf := math.Ceil(math.E / epsilon)
	if wf > float64(math.MaxUint32) {
		return nil, errors.New("countminsketch: epsilon too small")
	}
	df := math.Ceil(math.Log(1.0 / delta))
	if df > float64(math.MaxUint32) {
		return nil, errors.New("countminsketch: delta too small")
	}
	return newCMS(uint32(wf), uint32(df))
}

// NewCountMinSketchFromDimensions creates a CountMinSketch with explicit dimensions.
func NewCountMinSketchFromDimensions(width, depth uint32) (*CountMinSketch, error) {
	if width == 0 {
		return nil, errors.New("countminsketch: width must be > 0")
	}
	if depth == 0 {
		return nil, errors.New("countminsketch: depth must be > 0")
	}
	return newCMS(width, depth)
}

func newCMS(width, depth uint32) (*CountMinSketch, error) {
	table := make([][]uint64, depth)
	for i := range table {
		table[i] = make([]uint64, width)
	}
	return &CountMinSketch{width: width, depth: depth, table: table}, nil
}

func cmsHash(data []byte, seed uint32) uint64 {
	const (
		offset64 uint64 = 14695981039346656037
		prime64  uint64 = 1099511628211
	)
	h := offset64 ^ uint64(seed)*prime64
	for _, b := range data {
		h ^= uint64(b)
		h *= prime64
	}
	return h
}

// Add increments the count of data by delta.
func (c *CountMinSketch) Add(data []byte, delta uint64) {
	c.count += delta
	for i := uint32(0); i < c.depth; i++ {
		col := cmsHash(data, i) % uint64(c.width)
		c.table[i][col] += delta
	}
}

// Count returns the estimated frequency of data.
func (c *CountMinSketch) Count(data []byte) uint64 {
	var min uint64 = math.MaxUint64
	for i := uint32(0); i < c.depth; i++ {
		col := cmsHash(data, i) % uint64(c.width)
		if c.table[i][col] < min {
			min = c.table[i][col]
		}
	}
	return min
}

// TotalCount returns the sum of all added deltas.
func (c *CountMinSketch) TotalCount() uint64 { return c.count }

// Width returns the number of counters per row.
func (c *CountMinSketch) Width() uint32 { return c.width }

// Depth returns the number of hash functions / rows.
func (c *CountMinSketch) Depth() uint32 { return c.depth }

// Merge merges other into c. Both must have identical dimensions.
func (c *CountMinSketch) Merge(other *CountMinSketch) error {
	if c.width != other.width || c.depth != other.depth {
		return errors.New("countminsketch: cannot merge sketches with different dimensions")
	}
	c.count += other.count
	for i := uint32(0); i < c.depth; i++ {
		for j := uint32(0); j < c.width; j++ {
			c.table[i][j] += other.table[i][j]
		}
	}
	return nil
}
