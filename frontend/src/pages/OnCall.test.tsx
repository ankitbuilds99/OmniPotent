// The page read three keys the API never sent (`schedule_id`, `shifts`,
// `participants`), so the roster and the responder list were permanently empty
// and the schedule id fell back to the literal "default" — which every
// POST /oncall/overrides rejected with 422, silently, inside a console.error.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { OnCall } from './OnCall'

interface Call {
  url: string
  method: string
  body: unknown
}

let calls: Call[] = []

const TEAM = { id: 'team-1', name: 'Platform', timezone: 'UTC' }
const SCHEDULE_ID = '7e40711e-81df-427f-9c37-a5bc35757c23'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function currentOncall(overrides: Record<string, unknown> = {}) {
  const now = Date.now()
  return {
    primary: 'u-1',
    secondary: null,
    shadows: [],
    schedules: [{ schedule_id: SCHEDULE_ID, schedule_name: 'Primary', layer: 1, user_id: 'u-1' }],
    team_schedules: [
      { id: SCHEDULE_ID, name: 'Primary', layer: 1, timezone: 'UTC', rotation_type: 'weekly' },
    ],
    shifts: [
      {
        id: 'shift-1',
        schedule_id: SCHEDULE_ID,
        user_id: 'u-1',
        user_email: 'ada@acme.test',
        role: 'primary',
        start_time: new Date(now - 3600_000).toISOString(),
        end_time: new Date(now + 3600_000).toISOString(),
        source: 'shift',
        status: 'active',
      },
    ],
    participants: [
      { user_id: 'u-1', user_email: 'ada@acme.test', role: 'member' },
      { user_id: 'u-2', user_email: 'grace@acme.test', role: 'member' },
    ],
    ...overrides,
  }
}

function installFetch(handlers: Record<string, () => Response> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      for (const [prefix, fn] of Object.entries(handlers)) {
        const [m, p] = prefix.split(' ')
        if (m === method && url.startsWith(p)) return fn()
      }
      if (method === 'GET' && url === '/oncall/teams') return json([TEAM])
      if (method === 'GET' && url.endsWith('/current-oncall')) return json(currentOncall())
      if (method === 'GET' && url.endsWith('/escalation-policies')) return json([])
      if (method === 'GET' && url.includes('/shifts')) return json([])
      return json({ detail: `no route for ${method} ${url}` }, 404)
    })
  )
}

describe('OnCall', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders the on-call responder from the shifts the API returns', async () => {
    installFetch()
    render(<OnCall onBack={() => {}} />)

    expect((await screen.findAllByText('ada@acme.test')).length).toBeGreaterThan(0)
    expect(screen.getByText('primary')).toBeInTheDocument()
    // Not the old invented placeholder.
    expect(screen.queryByText('On-Call User')).not.toBeInTheDocument()
  })

  it('lists team members from `participants`, not a fabricated label', async () => {
    installFetch()
    render(<OnCall onBack={() => {}} />)

    await screen.findByText('Team Members')
    expect(await screen.findByText('grace@acme.test')).toBeInTheDocument()
    expect(screen.queryByText('Team Member')).not.toBeInTheDocument()
    expect(screen.queryByText('No team members found')).not.toBeInTheDocument()
  })

  it('posts a real schedule UUID when creating an override', async () => {
    installFetch({ 'POST /oncall/overrides': () => json({ id: 'ovr-1', status: 'active' }) })
    render(<OnCall onBack={() => {}} />)
    await screen.findAllByText('ada@acme.test')

    fireEvent.click(screen.getByRole('button', { name: /create override/i }))
    const select = await screen.findByRole('combobox')
    fireEvent.change(select, { target: { value: 'u-2' } })
    const [start, end] = screen.getAllByDisplayValue('')
    fireEvent.change(start, { target: { value: '2026-09-01T09:00' } })
    fireEvent.change(end, { target: { value: '2026-09-02T09:00' } })
    // The header button and the modal's submit share a label; the modal's is last.
    const submits = screen.getAllByRole('button', { name: 'Create Override' })
    fireEvent.click(submits[submits.length - 1])

    await waitFor(() => {
      const post = calls.find((c) => c.method === 'POST' && c.url === '/oncall/overrides')
      expect(post).toBeDefined()
      expect((post!.body as { schedule_id: string }).schedule_id).toBe(SCHEDULE_ID)
    })
    const post = calls.find((c) => c.method === 'POST' && c.url === '/oncall/overrides')!
    expect((post.body as { schedule_id: string }).schedule_id).not.toBe('default')
  })

  it('never offers an override when the team has no schedule', async () => {
    installFetch({
      'GET /oncall/teams/team-1/current-oncall': () =>
        json(currentOncall({ team_schedules: [], schedules: [], shifts: [], primary: null })),
    })
    render(<OnCall onBack={() => {}} />)

    await screen.findByText('grace@acme.test')
    expect(screen.getByRole('button', { name: /create override/i })).toBeDisabled()
    expect(calls.some((c) => c.method === 'POST' && c.url === '/oncall/overrides')).toBe(false)
  })

  it('shows the API error instead of an empty page', async () => {
    installFetch({
      'GET /oncall/teams': () => json({ detail: 'Missing or invalid API token.' }, 401),
    })
    render(<OnCall onBack={() => {}} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Missing or invalid API token.')
  })

  it('draws the week from real shift rows rather than round-robining participants', async () => {
    const day = new Date()
    day.setHours(12, 0, 0, 0)
    installFetch({
      'GET /oncall/schedules': () =>
        json([
          {
            id: 'shift-week-1',
            schedule_id: SCHEDULE_ID,
            user_id: 'u-2',
            start_time: new Date(day.getTime() - 3600_000).toISOString(),
            end_time: new Date(day.getTime() + 3600_000).toISOString(),
            shift_type: 'primary',
            status: 'scheduled',
          },
        ]),
    })
    render(<OnCall onBack={() => {}} />)

    await screen.findByText('This Week')
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.startsWith(`/oncall/schedules/${SCHEDULE_ID}/shifts?`))
      ).toBe(true)
    )
    // Six of the seven days have no shift, so they read "—" rather than
    // showing an invented responder.
    await waitFor(() => expect(screen.getAllByText('—')).toHaveLength(6))
  })
})

// `OncallEscalationStep.targets` is a JSON array of objects. The page typed it
// `string[]` and rendered `targets.join(", ")`, so the one line that tells you
// who gets paged read "Notify [object Object]".
describe('OnCall escalation policy', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  const POLICY = {
    id: 'pol-1',
    name: 'Severity 1',
    steps: [
      {
        step_number: 1,
        delay_minutes: 0,
        targets: [{ target_type: 'user', target_id: 'u-1', target_name: 'Ada Lovelace' }],
        notification_channels: ['slack', 'sms'],
      },
      {
        step_number: 2,
        delay_minutes: 15,
        targets: [
          { target_type: 'schedule', target_id: SCHEDULE_ID, target_name: 'Primary rotation' },
          { target_type: 'team', target_id: 'team-1' },
        ],
        notification_channels: ['voice'],
      },
    ],
  }

  function withPolicy() {
    installFetch({
      'GET /oncall/teams/team-1/escalation-policies': () => json([{ id: 'pol-1' }]),
      'GET /oncall/escalation-policies/pol-1': () => json(POLICY),
    })
  }

  it('names the people and rotations a step notifies', async () => {
    withPolicy()
    render(<OnCall onBack={() => {}} />)

    expect(await screen.findByText('Notify Ada Lovelace')).toBeInTheDocument()
    expect(
      screen.getByText('Notify Primary rotation (schedule), team-1 (team)')
    ).toBeInTheDocument()
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument()
  })

  it('numbers steps the way the server does', async () => {
    withPolicy()
    render(<OnCall onBack={() => {}} />)

    await screen.findByText('Severity 1')
    expect(screen.getByText('after 15min')).toBeInTheDocument()
  })

  it('says a step notifies nobody rather than inventing an "on-call" target', async () => {
    installFetch({
      'GET /oncall/teams/team-1/escalation-policies': () => json([{ id: 'pol-1' }]),
      'GET /oncall/escalation-policies/pol-1': () =>
        json({ ...POLICY, steps: [{ step_number: 1, delay_minutes: 0, targets: [], notification_channels: [] }] }),
    })
    render(<OnCall onBack={() => {}} />)

    expect(await screen.findByText('No targets configured')).toBeInTheDocument()
    expect(screen.queryByText('Notify on-call')).not.toBeInTheDocument()
  })
})

// Both arrows beside "This Week" were <button>s with no onClick, and the shifts
// effect depended on a useMemo with an empty dep array, so the grid was frozen
// on the current week even if the handlers had existed.
describe('OnCall week navigation', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  function windowsRequested(): string[] {
    return calls
      .filter((c) => c.url.includes('/shifts?'))
      .map((c) => new URL(c.url, 'http://x').searchParams.get('start_date')!)
  }

  it('fetches the following week when the forward arrow is clicked', async () => {
    installFetch()
    render(<OnCall onBack={() => {}} />)
    await screen.findByText('This Week')
    await waitFor(() => expect(windowsRequested()).toHaveLength(1))
    const thisWeek = windowsRequested()[0]

    fireEvent.click(screen.getByRole('button', { name: 'Next week' }))

    expect(await screen.findByText('Next Week')).toBeInTheDocument()
    await waitFor(() => expect(windowsRequested().length).toBeGreaterThan(1))
    const requested = windowsRequested()
    const nextWeek = requested[requested.length - 1]
    const days = (Date.parse(nextWeek) - Date.parse(thisWeek)) / 86_400_000
    expect(days).toBe(7)
  })

  it('goes back a week and returns with Today', async () => {
    installFetch()
    render(<OnCall onBack={() => {}} />)
    await screen.findByText('This Week')
    await waitFor(() => expect(windowsRequested()).toHaveLength(1))
    const thisWeek = windowsRequested()[0]

    fireEvent.click(screen.getByRole('button', { name: 'Previous week' }))
    expect(await screen.findByText('Last Week')).toBeInTheDocument()
    await waitFor(() => expect(windowsRequested().length).toBeGreaterThan(1))
    const requested = windowsRequested()
    const lastWeek = requested[requested.length - 1]
    expect((Date.parse(lastWeek) - Date.parse(thisWeek)) / 86_400_000).toBe(-7)

    fireEvent.click(screen.getByRole('button', { name: 'Today' }))
    expect(await screen.findByText('This Week')).toBeInTheDocument()
  })
})
