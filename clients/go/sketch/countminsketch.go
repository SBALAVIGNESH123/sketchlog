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

func (c *CountMinSketch) hash(data []byte, seed uint32) uint32 {
	h := fnv64a(data) ^ uint64(seed)*2654435761
	return uint32(h>>32) ^ uint32(h)
}

// Add increments the count of data by delta.
func (c *CountMinSketch) Add(data []byte, delta uint64) {
	for i := uint32(0); i < c.depth; i++ {
		j := c.hash(data, i) % c.width
		c.table[i][j] += delta
	}
	c.count += delta
}

// Count returns the estimated frequency of data.
func (c *CountMinSketch) Count(data []byte) uint64 {
	var min uint64 = ^uint64(0)
	for i := uint32(0); i < c.depth; i++ {
		j := c.hash(data, i) % c.width
		if c.table[i][j] < min {
			min = c.table[i][j]
		}
	}
	return min
}

// TotalCount returns the total number of items added.
func (c *CountMinSketch) TotalCount() uint64 { return c.count }

// Merge merges other into c. Both must have the same dimensions.
func (c *CountMinSketch) Merge(other *CountMinSketch) error {
	if c.width != other.width || c.depth != other.depth {
		return errors.New("countminsketch: cannot merge sketches with different dimensions")
	}
	for i := range c.table {
		for j := range c.table[i] {
			c.table[i][j] += other.table[i][j]
		}
	}
	c.count += other.count
	return nil
}
