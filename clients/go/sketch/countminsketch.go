package sketch

import (
	"errors"
	"hash/fnv"
	"math"
)

// CountMinSketch estimates the frequency of items in a data stream.
type CountMinSketch struct {
	width  uint32
	depth  uint32
	matrix [][]uint64
	total  uint64
}

const maxCMSDimension = 1 << 20 // 1 048 576 — prevent enormous allocations

// NewCountMinSketch creates a CMS with the given accuracy parameters.
// epsilon controls error (smaller = more accurate, more memory).
// delta controls confidence (smaller = higher confidence, more memory).
func NewCountMinSketch(epsilon, delta float64) (*CountMinSketch, error) {
	if epsilon <= 0 || epsilon >= 1 {
		return nil, errors.New("epsilon must be in (0, 1)")
	}
	if delta <= 0 || delta >= 1 {
		return nil, errors.New("delta must be in (0, 1)")
	}
	wf := math.Ceil(math.E / epsilon)
	df := math.Ceil(math.Log(1.0 / delta))
	if wf > maxCMSDimension || df > maxCMSDimension {
		return nil, errors.New("epsilon or delta too small: resulting dimensions exceed limit")
	}
	return NewCountMinSketchFromDimensions(uint32(wf), uint32(df))
}

// NewCountMinSketchFromDimensions creates a CMS with explicit width and depth.
func NewCountMinSketchFromDimensions(width, depth uint32) (*CountMinSketch, error) {
	if width == 0 || depth == 0 {
		return nil, errors.New("width and depth must be > 0")
	}
	if width > maxCMSDimension || depth > maxCMSDimension {
		return nil, errors.New("dimensions exceed maximum allowed limit")
	}
	if width > maxCMSDimension || depth > maxCMSDimension {
		return nil, errors.New("dimensions exceed maximum allowed limit")
	}
	matrix := make([][]uint64, depth)
	for i := range matrix {
		matrix[i] = make([]uint64, width)
	}
	return &CountMinSketch{width: width, depth: depth, matrix: matrix}, nil
}

func (c *CountMinSketch) hashes(data []byte) []uint32 {
	h := make([]uint32, c.depth)
	for i := uint32(0); i < c.depth; i++ {
		f := fnv.New32a()
		// Mix row index into hash seed
		_, _ = f.Write([]byte{byte(i), byte(i >> 8), byte(i >> 16), byte(i >> 24)})
		_, _ = f.Write(data)
		h[i] = f.Sum32() % c.width
	}
	return h
}

// Add increments the count of item by delta.
func (c *CountMinSketch) Add(item []byte, delta uint64) {
	c.total += delta
	for i, col := range c.hashes(item) {
		c.matrix[i][col] += delta
	}
}

// Count returns the estimated frequency of item.
func (c *CountMinSketch) Count(item []byte) uint64 {
	var min uint64 = math.MaxUint64
	for i, col := range c.hashes(item) {
		if v := c.matrix[i][col]; v < min {
			min = v
		}
	}
	return min
}

// TotalCount returns the total number of increments applied.
func (c *CountMinSketch) TotalCount() uint64 { return c.total }

// Merge combines another CountMinSketch into this one (same dimensions required).
func (c *CountMinSketch) Merge(other *CountMinSketch) error {
	if c.width != other.width || c.depth != other.depth {
		return errors.New("dimension mismatch")
	}
	c.total += other.total
	for i := range c.matrix {
		for j := range c.matrix[i] {
			c.matrix[i][j] += other.matrix[i][j]
		}
	}
	return nil
}
