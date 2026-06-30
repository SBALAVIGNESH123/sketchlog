import { createElement } from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('launch website', () => {
  it('renders the outcome-focused hero and primary navigation', () => {
    render(createElement(App))

    expect(
      screen.getByRole('heading', {
        level: 1,
        name: /bounded-memory metrics with explicit guarantees/i,
      }),
    ).toBeTruthy()
    expect(screen.getByRole('link', { name: 'View on GitHub' }))
      .toHaveProperty(
        'href',
        'https://github.com/SBALAVIGNESH123/sketchlog',
      )
    expect(screen.getByRole('link', { name: 'Explore Features' }))
      .toHaveProperty('hash', '#features')
  })

  it('renders all evidence-backed feature cards', () => {
    render(createElement(App))

    for (const feature of [
      'O(1) Memory Constraints',
      'Mathematical Accuracy',
      'C++ Core Speed',
      'Type-Safe SDKs',
      'DriftSketch Auto-Pilot',
      'Edge & WASM Ready',
    ]) {
      expect(screen.getByRole('heading', { level: 3, name: feature }))
        .toBeTruthy()
    }
  })
})
