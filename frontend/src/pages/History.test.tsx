// The API fallback path issued the literal string '/api/investigations?limit=50'.
// Neither the status dropdown nor the pager reached it, so "Next" advanced a
// counter and redrew the same first fifty rows, and selecting "Error" listed
// completed investigations. A non-OK response was ignored entirely: `if (res.ok)`
// with no else, leaving the previous list — [] on first load — on screen behind
// the "No investigations yet" empty state.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { History } from './History'

let urls: string[] = []

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function investigation(n: number) {
  return {
    id: `inv-${n}`,
    thread_id: `thread-${n}`,
    query: `Why is service ${n} slow?`,
    summary: null,
    status: 'completed',
    tokens_used: 1000 + n,
    latency_ms: 4200,
    feedback_rating: null,
    created_at: '2026-01-02T03:04:05+00:00',
    completed_at: '2026-01-02T03:05:05+00:00',
    report: null,
  }
}

/** `initSupabase()` resolves to null when Supabase is not configured, which is
 *  the branch that talks to our own API — the one the bug was in. */
vi.mock('../lib/supabase', () => ({ initSupabase: async () => null }))
vi.mock('../hooks/useSupabaseAuth', () => ({ useSupabaseAuth: () => ({ user: null }) }))

function installFetch(reply: (url: string) => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      urls.push(url)
      return reply(url)
    })
  )
}

describe('History', () => {
  beforeEach(() => {
    urls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('asks the API for the page it is showing', async () => {
    installFetch(() => json({ investigations: [investigation(1)] }))
    render(<History onBack={() => {}} />)

    await screen.findByText('Why is service 1 slow?')
    const url = new URL(urls[0], 'http://localhost')
    expect(url.searchParams.get('limit')).toBe('10')
    expect(url.searchParams.get('offset')).toBe('0')
  })

  it('sends the status filter to the API', async () => {
    installFetch(() => json({ investigations: [investigation(1)] }))
    render(<History onBack={() => {}} />)
    await screen.findByText('Why is service 1 slow?')

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'error' } })

    await waitFor(() => {
      const last = new URL(urls[urls.length - 1], 'http://localhost')
      expect(last.searchParams.get('status')).toBe('error')
    })
  })

  it('advances the offset when paging forward', async () => {
    // A full page back, so "Next" is enabled.
    const page = Array.from({ length: 10 }, (_, i) => investigation(i + 1))
    installFetch(() => json({ investigations: page }))
    render(<History onBack={() => {}} />)
    await screen.findByText('Why is service 1 slow?')

    fireEvent.click(screen.getByText('Next'))

    await waitFor(() => {
      const last = new URL(urls[urls.length - 1], 'http://localhost')
      expect(last.searchParams.get('offset')).toBe('10')
    })
  })

  it('does not offer a next page when the last one came back short', async () => {
    installFetch(() => json({ investigations: [investigation(1)] }))
    render(<History onBack={() => {}} />)
    await screen.findByText('Why is service 1 slow?')

    expect(screen.getByText('Next').closest('button')).toBeDisabled()
  })

  it('keeps paging available when a page-local filter hides rows', async () => {
    // "Next" used to be disabled by `filteredInvestigations.length < pageSize`,
    // so typing a search term that matched three of ten rows disabled paging.
    const page = Array.from({ length: 10 }, (_, i) => investigation(i + 1))
    installFetch(() => json({ investigations: page }))
    render(<History onBack={() => {}} />)
    await screen.findByText('Why is service 1 slow?')

    fireEvent.change(screen.getByPlaceholderText('Filter this page...'), {
      target: { value: 'service 3' },
    })

    expect(screen.getByText('Next').closest('button')).not.toBeDisabled()
  })

  it('reports a failed load instead of showing an empty history', async () => {
    installFetch(() => json({ detail: 'Not authenticated' }, 401))
    render(<History onBack={() => {}} />)

    expect(await screen.findByText(/Not authenticated/)).toBeInTheDocument()
    // The empty state is the wrong thing to say when the request never landed.
    expect(screen.queryByText('No investigations yet')).not.toBeInTheDocument()
  })

  it('distinguishes an empty filter result from an empty history', async () => {
    installFetch(() => json({ investigations: [] }))
    render(<History onBack={() => {}} />)

    // Nothing at all, no filter: the history really is empty.
    await screen.findByText('No investigations yet')
    expect(
      screen.getByText('Start an investigation from the console to see it here.')
    ).toBeInTheDocument()

    // Nothing under a filter: saying "start an investigation" would be wrong —
    // there may be plenty, just none with this status.
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'error' } })

    expect(await screen.findByText('Nothing to show here')).toBeInTheDocument()
    expect(screen.getByText('No investigations with status "error".')).toBeInTheDocument()
  })
})
