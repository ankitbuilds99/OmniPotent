// The preferences API says two things this page used to throw away: which
// channels this build cannot deliver on at all (`unavailable`), and which events
// nothing in it can raise (`unproduced`). Without them the page told an operator
// that Push was merely missing credentials — for a channel with no fields to
// fill in — and offered pre-checked Slack and Email switches for deployment
// events, then said "Saved!". The only way to discover none of it worked was to
// wait for a notification that never came.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { NotificationPreferences } from './NotificationPreferences'

const PUSH_REASON =
  'Browser push needs a registered device, and this build has no endpoint for a browser to register one.'
const DEPLOY_REASON =
  'This build has no deployments feature to raise it. Deploy timings are read from Argo CD for incident correlation only.'

/** Shaped exactly like GET /api/notifications/preferences (app/notifications/preferences.py). */
const PREFS = {
  channels: [
    {
      id: 'email',
      name: 'Email',
      description: 'Email notifications',
      icon: 'mail',
      enabled: true,
      configured: true,
      unavailable: '',
      fields: [
        {
          key: 'address',
          label: 'Email Address',
          type: 'email',
          secret: false,
          required: true,
          value: 'ops@acme.test',
          is_set: true,
        },
      ],
    },
    {
      id: 'slack',
      name: 'Slack',
      description: 'Slack workspace messages',
      icon: 'message-square',
      enabled: true,
      configured: false,
      unavailable: '',
      fields: [
        {
          key: 'webhook',
          label: 'Webhook URL',
          type: 'url',
          secret: true,
          required: true,
          value: '',
          is_set: false,
        },
      ],
    },
    {
      id: 'push',
      name: 'Push',
      description: 'Browser push notifications',
      icon: 'bell',
      enabled: true,
      configured: false,
      unavailable: PUSH_REASON,
      fields: [],
    },
  ],
  rules: [
    {
      event: 'incident_created',
      description: 'New incident created',
      unproduced: '',
      channels: { email: true, slack: true, push: true },
    },
    {
      event: 'deployment_started',
      description: 'Deployment started',
      unproduced: DEPLOY_REASON,
      channels: { email: false, slack: true, push: false },
    },
  ],
  quiet_hours: { enabled: false, start: '22:00', end: '08:00' },
  digest_mode: 'realtime',
  customised: false,
}

interface Call {
  url: string
  method: string
  body: unknown
}

let calls: Call[] = []

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(prefs: unknown = PREFS) {
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.endsWith('/api/notifications/preferences')) return json(prefs)
    return json({ detail: `no route for ${method} ${url}` }, 404)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

/** The matrix switch for one event/channel pair. */
function matrixSwitch(event: string, channel: string): HTMLButtonElement {
  return screen.getByRole('button', { name: `${event} via ${channel}` }) as HTMLButtonElement
}

describe('NotificationPreferences', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  describe('a channel this build cannot deliver on', () => {
    it('gives the reason instead of blaming missing credentials', async () => {
      installFetch()
      render(<NotificationPreferences onBack={() => {}} />)

      expect(await screen.findByText(PUSH_REASON)).toBeInTheDocument()
      // There is no Configure button for Push — it has no fields — so this
      // instruction named a repair the reader could never carry out.
      expect(
        screen.queryByText('Browser push notifications — credentials missing')
      ).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Push enabled but not configured')).not.toBeInTheDocument()
      expect(screen.getByLabelText('Push cannot deliver in this build')).toBeInTheDocument()
    })

    it('withdraws its routing switches while still showing what was asked for', async () => {
      installFetch()
      render(<NotificationPreferences onBack={() => {}} />)
      await screen.findByText(PUSH_REASON)

      const push = matrixSwitch('incident_created', 'Push')
      expect(push).toBeDisabled()
      expect(push).toHaveAttribute('title', PUSH_REASON)
      // The saved routing is still on screen: "I asked for this and heard
      // nothing" is the thing the reader came to check.
      expect(push).toHaveAttribute('aria-pressed', 'true')

      fireEvent.click(push)
      expect(push).toHaveAttribute('aria-pressed', 'true')
    })

    it('still warns about a channel whose credentials really are missing', async () => {
      installFetch()
      render(<NotificationPreferences onBack={() => {}} />)

      // Slack is deliverable and unconfigured — that warning is actionable and
      // must survive the fix.
      expect(
        await screen.findByText('Slack workspace messages — credentials missing')
      ).toBeInTheDocument()
      expect(screen.getByLabelText('Slack enabled but not configured')).toBeInTheDocument()
      expect(matrixSwitch('incident_created', 'Slack')).toBeEnabled()
    })
  })

  describe('an event nothing in this build raises', () => {
    it('says so on the row', async () => {
      installFetch()
      render(<NotificationPreferences onBack={() => {}} />)

      expect(await screen.findByText(DEPLOY_REASON)).toBeInTheDocument()
    })

    it('stops offering routing switches for it', async () => {
      installFetch()
      render(<NotificationPreferences onBack={() => {}} />)
      await screen.findByText(DEPLOY_REASON)

      for (const channel of ['Email', 'Slack', 'Push']) {
        const cell = matrixSwitch('deployment_started', channel)
        expect(cell).toBeDisabled()
        expect(cell).toHaveAttribute('title', DEPLOY_REASON)
      }
      // Pre-checked and unreachable: the page used to let this be toggled and
      // saved, and confirm the save, for something that can never fire.
      expect(matrixSwitch('deployment_started', 'Slack')).toHaveAttribute('aria-pressed', 'true')
    })

    it('leaves an event that is raised fully switchable', async () => {
      installFetch()
      render(<NotificationPreferences onBack={() => {}} />)
      await screen.findByText(DEPLOY_REASON)

      const email = matrixSwitch('incident_created', 'Email')
      expect(email).toBeEnabled()
      expect(email).toHaveAttribute('aria-pressed', 'true')

      fireEvent.click(email)
      await waitFor(() => expect(email).toHaveAttribute('aria-pressed', 'false'))
    })
  })

  it('tolerates a server that sends neither field', async () => {
    // An older backend omits both keys. Nothing is claimed to be undeliverable,
    // and the page behaves as it did before.
    const legacy = {
      ...PREFS,
      channels: PREFS.channels.map(({ unavailable: _unavailable, ...rest }) => rest),
      rules: PREFS.rules.map(({ unproduced: _unproduced, ...rest }) => rest),
    }
    installFetch(legacy)
    render(<NotificationPreferences onBack={() => {}} />)

    expect(await screen.findByText('New incident created')).toBeInTheDocument()
    expect(screen.queryByText('cannot deliver')).not.toBeInTheDocument()
    expect(matrixSwitch('deployment_started', 'Slack')).toBeEnabled()
  })
})
