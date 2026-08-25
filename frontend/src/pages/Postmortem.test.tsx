// The page built the whole document client-side: Root Cause Analysis was always
// the literal "The incident was caused by an issue that requires further
// investigation…", Lessons Learned was always the same block, both action items
// were hardcoded, and all of it carried an "AI Generated" badge.
// `regenerateSection` awaited a 2000ms timer and issued no request.

import { render, screen, fireEvent } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Postmortem } from './Postmortem'

interface Call {
  url: string
  method: string
  body: unknown
}

let calls: Call[] = []

const INCIDENT = {
  id: 'c0ffee00-0000-4000-8000-000000000001',
  title: 'Checkout API returning 500s',
  status: 'resolved',
  severity: 'critical',
  service: 'checkout-api',
}

const DOCUMENT = {
  id: `pm-${INCIDENT.id}`,
  incident_id: INCIDENT.id,
  title: INCIDENT.title,
  status: 'draft',
  severity: 'SEV1',
  generated_at: '2026-01-02T03:04:05+00:00',
  authors: ['alice'],
  reviewers: [],
  sections: [
    { id: 'summary', title: 'Executive Summary', content: 'Checkout was down.', source: 'model' },
    {
      id: 'root_cause',
      title: 'Root Cause Analysis',
      content: 'A leaked transaction exhausted the connection pool.',
      source: 'model',
    },
  ],
  action_items: [
    {
      id: 'a1',
      title: 'Alert on pool saturation',
      owner: 'alice',
      priority: 'P1',
      status: 'open',
      due_date: null,
      type: 'detect',
    },
  ],
  timeline: [
    {
      timestamp: '2026-01-02T03:00:00+00:00',
      event: 'Incident created: Checkout API returning 500s',
      actor: null,
      type: 'detection',
    },
  ],
  metrics: {
    time_to_detect: null,
    time_to_acknowledge: 4,
    time_to_mitigate: null,
    time_to_resolve: 31,
    affected_services: ['checkout-api'],
    customer_impact: true,
    impact: null,
  },
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(overrides: Record<string, () => Response> = {}) {
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /api/incidents') return json([INCIDENT])
    if (key === `POST /api/incidents/${INCIDENT.id}/postmortem`) return json(DOCUMENT)
    return json({ detail: `no route for ${key}` }, 405)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

describe('Postmortem', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders the server-generated root cause, not the hardcoded sentence', async () => {
    installFetch()
    render(<Postmortem onBack={() => {}} />)

    expect(
      await screen.findByText('A leaked transaction exhausted the connection pool.')
    ).toBeInTheDocument()
    expect(screen.queryByText(/requires further investigation/)).not.toBeInTheDocument()
    expect(screen.queryByText(/What Went Well/)).not.toBeInTheDocument()

    const post = calls.find(c => c.method === 'POST')
    expect(post?.url).toBe(`/api/incidents/${INCIDENT.id}/postmortem`)
  })

  it('shows "—" for a duration the record cannot measure instead of 0m', async () => {
    installFetch()
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')

    // time_to_detect and time_to_mitigate are null; the old page printed "0m".
    expect(screen.getAllByText('—')).toHaveLength(2)
    expect(screen.queryByText('0m')).not.toBeInTheDocument()
    expect(screen.getByText('4m')).toBeInTheDocument()
    expect(screen.getByText('31m')).toBeInTheDocument()
  })

  it('regenerating a section calls the API for that section and swaps the text in', async () => {
    installFetch({
      [`POST /api/incidents/${INCIDENT.id}/postmortem`]: () => {
        const nth = calls.filter(c => c.method === 'POST').length
        if (nth === 1) return json(DOCUMENT)
        return json({
          sections: [
            {
              id: 'root_cause',
              title: 'Root Cause Analysis',
              content: 'Regenerated: a migration held an exclusive lock.',
              source: 'model',
            },
          ],
        })
      },
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('A leaked transaction exhausted the connection pool.')

    fireEvent.click(screen.getByLabelText('Regenerate Root Cause Analysis'))

    expect(
      await screen.findByText('Regenerated: a migration held an exclusive lock.')
    ).toBeInTheDocument()
    const posts = calls.filter(c => c.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[1].body).toEqual({ sections: ['root_cause'] })
  })

  it('surfaces a failed regeneration instead of silently doing nothing', async () => {
    installFetch({
      [`POST /api/incidents/${INCIDENT.id}/postmortem`]: () => {
        const nth = calls.filter(c => c.method === 'POST').length
        if (nth === 1) return json(DOCUMENT)
        return json({ detail: { error: 'generation_failed', message: 'the model call failed' } }, 502)
      },
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('A leaked transaction exhausted the connection pool.')

    fireEvent.click(screen.getByLabelText('Regenerate Root Cause Analysis'))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/the model call failed/)
    // The original text is still there — nothing was replaced by a fake.
    expect(
      screen.getByText('A leaked transaction exhausted the connection pool.')
    ).toBeInTheDocument()
  })

  it('reports that no model is configured rather than showing a template', async () => {
    installFetch({
      [`POST /api/incidents/${INCIDENT.id}/postmortem`]: () =>
        json(
          {
            detail: {
              error: 'llm_not_configured',
              message: 'Postmortem generation needs an LLM: set ANTHROPIC_API_KEY.',
              missing_configuration: ['ANTHROPIC_API_KEY'],
            },
          },
          503
        ),
    })
    render(<Postmortem onBack={() => {}} />)

    expect(await screen.findByText(/set ANTHROPIC_API_KEY/)).toBeInTheDocument()
    expect(screen.queryByText(/requires further investigation/)).not.toBeInTheDocument()
  })

  it('does not stamp an invented due date on an action item', async () => {
    installFetch()
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')

    fireEvent.click(screen.getByText('Actions'))

    expect(await screen.findByText('Alert on pool saturation')).toBeInTheDocument()
    expect(screen.getByText('Not set')).toBeInTheDocument()
    expect(screen.queryByText('Review and update monitoring alerts')).not.toBeInTheDocument()
  })
})
