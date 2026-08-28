// The landing page's "Operations Center" illustration.
//
// It was headed "Real-time monitoring" and its log panel carried a pulsing dot
// captioned "streaming", drawing canned lines with `Math.random()` and stamping
// them with `new Date()` — so a made-up Redis timeout appeared to have happened
// seconds ago, on the visitor's own clock, on a page that measures nothing.
//
// These tests pin the two properties that fix bought: the replay is a fixed
// script carrying its own timestamps, and nothing claims to be live.

import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dashboard } from './Dashboard'

describe('landing Operations Center', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('says the figures are the bundled demo, not live infrastructure', () => {
    render(<Dashboard />)

    expect(screen.getByText(/not live infrastructure/)).toBeInTheDocument()
    expect(screen.queryByText('Real-time monitoring')).not.toBeInTheDocument()
    expect(screen.queryByText('streaming')).not.toBeInTheDocument()
  })

  it('replays the incident in script order with the script’s own timestamps', () => {
    vi.useFakeTimers()
    const started = new Date('2031-04-05T09:15:00Z')
    vi.setSystemTime(started)

    render(<Dashboard />)
    act(() => void vi.advanceTimersByTime(2000))

    expect(screen.getByText('14:02:11')).toBeInTheDocument()
    expect(
      screen.getByText('Deploy checkout-svc 1.8.0 started (discount rollout)')
    ).toBeInTheDocument()

    // The next line is the next entry, every time — not a random draw.
    act(() => void vi.advanceTimersByTime(2000))
    expect(screen.getByText('14:03:48')).toBeInTheDocument()

    // And no line is stamped with the visitor's clock, which is what made the
    // fabricated events read as things that had just happened to them.
    const now = started.toTimeString().slice(0, 5)
    expect(screen.queryByText(new RegExp(`^${now}`))).toBeNull()
  })

  it('derives each trend badge from the series drawn beneath it', () => {
    render(<Dashboard />)

    // cpuData runs 45 -> 65 across the window: +44%, where the hand-set badge
    // claimed +5%. Every card's badge is now a fact about its own sparkline.
    expect(screen.getByText('+44%')).toBeInTheDocument()
    // networkData 120 -> 205.
    expect(screen.getByText('+71%')).toBeInTheDocument()
    expect(screen.queryByText('+5%')).toBeNull()
    expect(screen.queryByText('+12%')).toBeNull()
  })
})
