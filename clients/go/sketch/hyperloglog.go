// Package sketch provides native Go implementations of probabilistic data
// structures for embedded use — no network connection required.
package sketch

import (
	"errors"
	"math"
	"math/bits"
)

// HyperLogLog estimates the cardinality (number of distinct elements) of a
// multiset using O(2^precision) bytes of memory.
//
// For precision p (4 ≤ p ≤ 18) the standard error is ≈ 1.04/√(2^p).
// A typical value is p=14, giving ~0.81% error with 16 KiB of memory.
type HyperLogLog struct {
	precision uint8
	m         uint32 // number of registers = 2^precision
	registers []uint8
	alpha     float64
}

// NewHyperLogLog creates a HyperLogLog with the given precision (4 ≤ p ≤ 18).
func NewHyperLogLog(precision uint8) (*HyperLogLog, error) {
	if precision < 4 || precision > 18 {
		return nil, errors.New("hyperloglog: precision must be in [4, 18]")
	}
	m := uint32(1) << precision
	alpha := hllAlpha(m)
	return &HyperLogLog{
		precision: precision,
		m:         m,
		registers: make([]uint8, m),
		alpha:     alpha,
	}, nil
}

func hllAlpha(m uint32) float64 {
	switch m {
	case 16:
		return 0.673
	case 32:
		return 0.697
	case 64:
		return 0.709
	default:
		return 0.7213 / (1.0 + 1.079/float64(m))
	}
}

// fnv64a hashes a byte slice using FNV-1a (64-bit).
func fnv64a(data []byte) uint64 {
	const (
		offset64 uint64 = 14695981039346656037
		prime64  uint64 = 1099511628211
	)
	h := offset64
	for _, b := range data {
		h ^= uint64(b)
		h *= prime64
	}
	return h
}

// Add adds a byte slice to the sketch.
func (h *HyperLogLog) Add(data []byte) {
	x := fnv64a(data)
	j := x >> (64 - uint(h.precision)) // top p bits → register index
	// leading zeros of the remaining (64-p) bits + 1
	rho := uint8(bits.LeadingZeros64(x<<uint(h.precision))) + 1
	if rho > h.registers[j] {
		h.registers[j] = rho
	}
}

// Count returns the estimated cardinality.
func (h *HyperLogLog) Count() uint64 {
	m := float64(h.m)
	sum := 0.0
	for _, v := range h.registers {
		sum += math.Pow(2, -float64(v))
	}
	estimate := h.alpha * m * m / sum

	// Small range correction: use linear counting when estimate is low
	// and there are empty registers.
	if estimate <= 2.5*m {
		zeros := 0
		for _, v := range h.registers {
			if v == 0 {
				zeros++
			}
		}
		if zeros > 0 {
			estimate = m * math.Log(m/float64(zeros))
		}
	}
	// Large range correction is omitted: FNV-1a produces 64-bit hashes,
	// so the collision threshold (~2^64/30) is unreachable in practice.

	return uint64(estimate)
}

// Merge merges other into h. Both must have the same precision.
func (h *HyperLogLog) Merge(other *HyperLogLog) error {
	if h.precision != other.precision {
		return errors.New("hyperloglog: cannot merge sketches with different precision")
	}
	for i, v := range other.registers {
		if v > h.registers[i] {
			h.registers[i] = v
		}
	}
	return nil
}
