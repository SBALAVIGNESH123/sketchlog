package sketch

import (
	"errors"
	"hash/fnv"
	"math"
	"math/bits"
)

// HyperLogLog estimates the cardinality of a multiset.
type HyperLogLog struct {
	p         uint8
	m         uint32
	registers []uint8
	alpha     float64
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
		return 0.7213 / (1 + 1.079/float64(m))
	}
}

// NewHyperLogLog creates a HyperLogLog with precision p (4 <= p <= 18).
func NewHyperLogLog(p uint8) (*HyperLogLog, error) {
	if p < 4 || p > 18 {
		return nil, errors.New("precision p must be in [4, 18]")
	}
	m := uint32(1) << p
	return &HyperLogLog{
		p:         p,
		m:         m,
		registers: make([]uint8, m),
		alpha:     hllAlpha(m),
	}, nil
}

func (h *HyperLogLog) hash(data []byte) uint64 {
	f := fnv.New64a()
	_, _ = f.Write(data)
	return f.Sum64()
}

// Add adds a data item to the sketch.
func (h *HyperLogLog) Add(data []byte) {
	x := h.hash(data)
	j := x >> (64 - uint(h.p))
	w := x << uint(h.p)
	rho := uint8(bits.LeadingZeros64(w)) + 1
	if rho > h.registers[j] {
		h.registers[j] = rho
	}
}

// Count returns the estimated cardinality.
func (h *HyperLogLog) Count() uint64 {
	var sum float64
	for _, v := range h.registers {
		sum += math.Pow(2, -float64(v))
	}
	m := float64(h.m)
	estimate := h.alpha * m * m / sum

	// Small range correction
	if estimate <= 2.5*m {
		var zeros int
		for _, v := range h.registers {
			if v == 0 {
				zeros++
			}
		}
		if zeros > 0 {
			estimate = m * math.Log(m/float64(zeros))
		}
	}

	return uint64(estimate)
}

// Merge combines another HyperLogLog into this one (must have same precision).
func (h *HyperLogLog) Merge(other *HyperLogLog) error {
	if h.p != other.p {
		return errors.New("cannot merge HyperLogLog sketches with different precision")
	}
	for i, v := range other.registers {
		if v > h.registers[i] {
			h.registers[i] = v
		}
	}
	return nil
}
