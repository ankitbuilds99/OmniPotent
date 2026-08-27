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
    // The default: nothing stored yet, so the page generates. Tests that care
    // about the stored path override this key.
    if (key === `GET /api/incidents/${INCIDENT.id}/postmortem`) {
      return json({ detail: 'No postmortem has been generated for this incident yet.' }, 404)
    }
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

  // The page used to POST unconditionally on every visit, so it re-ran the
  // model each time and the fresh draft overwrote whatever had been edited.
  it('reads the stored document instead of regenerating when one exists', async () => {
    installFetch({
      [`GET /api/incidents/${INCIDENT.id}/postmortem`]: () =>
        json({
          ...DOCUMENT,
          sections: [
            DOCUMENT.sections[0],
            { ...DOCUMENT.sections[1], content: "The operator's own analysis.", source: 'edited' },
          ],
        }),
    })
    render(<Postmortem onBack={() => {}} />)

    expect(await screen.findByText("The operator's own analysis.")).toBeInTheDocument()
    expect(calls.filter(c => c.method === 'POST')).toHaveLength(0)
  })

  // Save was `setPostmortem(...)` and nothing else — the edit lived in component
  // state until the next reload discarded it.
  it('saving a section PATCHes it to the server', async () => {
    installFetch({
      [`PATCH /api/incidents/${INCIDENT.id}/postmortem/sections/root_cause`]: () =>
        json({
          ...DOCUMENT,
          sections: [
            DOCUMENT.sections[0],
            { ...DOCUMENT.sections[1], content: 'A migration held an exclusive lock.', source: 'edited' },
          ],
        }),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('A leaked transaction exhausted the connection pool.')

    fireEvent.click(screen.getByLabelText('Edit Root Cause Analysis'))
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'A migration held an exclusive lock.' },
    })
    fireEvent.click(screen.getByText('Save'))

    expect(await screen.findByText('A migration held an exclusive lock.')).toBeInTheDocument()
    const patch = calls.find(c => c.method === 'PATCH')
    expect(patch?.url).toBe(`/api/incidents/${INCIDENT.id}/postmortem/sections/root_cause`)
    expect(patch?.body).toEqual({ content: 'A migration held an exclusive lock.' })
  })

  it('a failed save says so and hands the text back rather than dropping it', async () => {
    installFetch({
      [`PATCH /api/incidents/${INCIDENT.id}/postmortem/sections/root_cause`]: () =>
        json({ detail: 'This postmortem has no root_cause section.' }, 404),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('A leaked transaction exhausted the connection pool.')

    fireEvent.click(screen.getByLabelText('Edit Root Cause Analysis'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Rewritten.' } })
    fireEvent.click(screen.getByText('Save'))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/no root_cause section/)
    // Back in the editor, with what they typed still in it.
    expect(await screen.findByRole('textbox')).toHaveValue('Rewritten.')
  })

  // Same defect on the other control: the status dropdown never left the browser.
  it('changing an action item status PATCHes it to the server', async () => {
    installFetch({
      [`PATCH /api/incidents/${INCIDENT.id}/postmortem/action-items/a1`]: () =>
        json({
          ...DOCUMENT,
          action_items: [{ ...DOCUMENT.action_items[0], status: 'completed' }],
        }),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')

    fireEvent.click(screen.getByText('Actions'))
    await screen.findByText('Alert on pool saturation')
    fireEvent.change(screen.getByDisplayValue('Open'), { target: { value: 'completed' } })

    const patch = await vi.waitFor(() => {
      const found = calls.find(c => c.method === 'PATCH')
      expect(found).toBeDefined()
      return found!
    })
    expect(patch.url).toBe(`/api/incidents/${INCIDENT.id}/postmortem/action-items/a1`)
    expect(patch.body).toEqual({ status: 'completed' })
  })
})

// Both "+ Add Item" buttons and the header's Publish were `<button>`s with no
// onClick — the two things a review meeting actually produces, a decision to
// sign off and the follow-ups nobody thought of beforehand.
describe('Postmortem review actions', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  const ADDED = {
    id: 'a2',
    title: 'Run a game day',
    owner: 'carol',
    priority: 'P1',
    status: 'open',
    due_date: null,
    type: 'mitigate',
  }

  it('adding an item POSTs what was typed and shows it in the table', async () => {
    installFetch({
      [`POST /api/incidents/${INCIDENT.id}/postmortem/action-items`]: () =>
        json({ ...DOCUMENT, action_items: [...DOCUMENT.action_items, ADDED] }),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')
    fireEvent.click(screen.getByText('Actions'))
    await screen.findByText('Alert on pool saturation')

    fireEvent.click(screen.getByText('+ Add Item'))
    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'Run a game day' } })
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'carol' } })
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'P1' } })
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'mitigate' } })
    fireEvent.click(screen.getByText('Add'))

    expect(await screen.findByText('Run a game day')).toBeInTheDocument()
    const post = calls.find(c => c.url.endsWith('/postmortem/action-items'))
    expect(post?.method).toBe('POST')
    expect(post?.body).toEqual({
      title: 'Run a game day',
      owner: 'carol',
      priority: 'P1',
      type: 'mitigate',
      // Nothing was entered, so nothing is claimed to have been agreed.
      due_date: null,
    })
  })

  it('sends a due date only when one was entered', async () => {
    installFetch({
      [`POST /api/incidents/${INCIDENT.id}/postmortem/action-items`]: () =>
        json({ ...DOCUMENT, action_items: [...DOCUMENT.action_items, ADDED] }),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')
    fireEvent.click(screen.getByText('Actions'))
    await screen.findByText('Alert on pool saturation')

    fireEvent.click(screen.getByText('+ Add Item'))
    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'Cap pool size' } })
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-03-01' } })
    fireEvent.click(screen.getByText('Add'))

    await vi.waitFor(() =>
      expect(calls.some(c => c.url.endsWith('/postmortem/action-items'))).toBe(true)
    )
    const post = calls.find(c => c.url.endsWith('/postmortem/action-items'))!
    const body = post.body as { due_date: string | null }
    expect(body.due_date).not.toBeNull()
    expect(body.due_date!.startsWith('2026-03-01')).toBe(true)
  })

  it('refuses to add an item with no title', async () => {
    installFetch()
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')
    fireEvent.click(screen.getByText('Actions'))
    await screen.findByText('Alert on pool saturation')

    fireEvent.click(screen.getByText('+ Add Item'))
    expect(screen.getByText('Add')).toBeDisabled()
    fireEvent.click(screen.getByText('Add'))

    expect(calls.some(c => c.url.endsWith('/postmortem/action-items'))).toBe(false)
  })

  it('says why an item could not be added instead of dropping it silently', async () => {
    installFetch({
      [`POST /api/incidents/${INCIDENT.id}/postmortem/action-items`]: () =>
        json({ detail: 'an action item needs a title' }, 422),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')
    fireEvent.click(screen.getByText('Actions'))
    await screen.findByText('Alert on pool saturation')

    fireEvent.click(screen.getByText('+ Add Item'))
    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'Something' } })
    fireEvent.click(screen.getByText('Add'))

    expect(await screen.findByRole('alert')).toHaveTextContent(/an action item needs a title/)
    expect(screen.queryByText('Something')).not.toBeInTheDocument()
  })

  it('publishing PATCHes the status and re-badges the document', async () => {
    installFetch({
      [`PATCH /api/incidents/${INCIDENT.id}/postmortem`]: () =>
        json({ ...DOCUMENT, status: 'published' }),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')
    expect(screen.getByText('draft')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Publish'))

    // The badge follows the server's answer, and the button becomes the way back.
    expect(await screen.findByText('published')).toBeInTheDocument()
    expect(await screen.findByText('Unpublish')).toBeInTheDocument()
    const patch = calls.find(c => c.method === 'PATCH')
    expect(patch?.url).toBe(`/api/incidents/${INCIDENT.id}/postmortem`)
    expect(patch?.body).toEqual({ status: 'published' })
  })

  it('a published document can be sent back to draft', async () => {
    installFetch({
      [`GET /api/incidents/${INCIDENT.id}/postmortem`]: () =>
        json({ ...DOCUMENT, status: 'published' }),
      [`PATCH /api/incidents/${INCIDENT.id}/postmortem`]: () => json(DOCUMENT),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')

    fireEvent.click(await screen.findByText('Unpublish'))

    expect(await screen.findByText('Publish')).toBeInTheDocument()
    expect(calls.find(c => c.method === 'PATCH')?.body).toEqual({ status: 'draft' })
  })

  it('a failed publish leaves the badge alone and says so', async () => {
    installFetch({
      [`PATCH /api/incidents/${INCIDENT.id}/postmortem`]: () =>
        json({ detail: 'No postmortem has been generated for this incident yet.' }, 404),
    })
    render(<Postmortem onBack={() => {}} />)
    await screen.findByText('Checkout was down.')

    fireEvent.click(screen.getByText('Publish'))

    expect(await screen.findByRole('alert')).toHaveTextContent(/No postmortem has been generated/)
    expect(screen.getByText('draft')).toBeInTheDocument()
    expect(screen.getByText('Publish')).toBeInTheDocument()
  })
})
