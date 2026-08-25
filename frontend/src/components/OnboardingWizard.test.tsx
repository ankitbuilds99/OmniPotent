// The playground step POSTed /api/chat {prompt} — a route that does not exist
// (405) — and parsed event types the backend never emits, so every run landed
// in the catch and replayed a hardcoded "Root cause: Database connection pool
// exhaustion after v2.4.1 deploy / Confidence: 87%" script. These tests pin
// that the wizard talks to the real /chat/stream contract and that a failure
// is reported as a failure.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { OnboardingWizard } from './OnboardingWizard'

interface Call {
  url: string
  method: string
  body: unknown
}

const calls: Call[] = []

function sse(frames: unknown[]): Response {
  const text = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('')
  return new Response(new TextEncoder().encode(text), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function installFetch(respond: (url: string) => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({
        url,
        method: (init?.method ?? 'GET').toUpperCase(),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      })
      return respond(url)
    })
  )
}

async function openPlayground() {
  render(<OnboardingWizard onComplete={() => {}} onSkip={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: /try the playground first/i }))
  fireEvent.click(await screen.findByRole('button', { name: /run investigation/i }))
}

afterEach(() => {
  calls.length = 0
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('OnboardingWizard playground', () => {
  it('streams the real /chat/stream endpoint with a {thread_id, message} body', async () => {
    installFetch(() =>
      sse([
        { type: 'trace', line: 'Querying Prometheus for checkout-service' },
        { type: 'done', status: 'completed', answer: 'No anomaly found.' },
      ])
    )
    await openPlayground()

    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    const call = calls[0]
    expect(call.url).toBe('/chat/stream')
    expect(call.method).toBe('POST')
    expect(call.body).toMatchObject({ message: expect.stringContaining('checkout-service') })
    expect((call.body as { thread_id: string }).thread_id).toBeTruthy()
    expect(call.body).not.toHaveProperty('prompt')
  })

  it('renders the trace lines and conclusion the server actually sent', async () => {
    installFetch(() =>
      sse([
        { type: 'trace', line: 'Querying Prometheus for checkout-service' },
        {
          type: 'done',
          status: 'completed',
          answer: 'Long form answer',
          report: { root_cause: 'Upstream TLS certificate expired' },
        },
      ])
    )
    await openPlayground()

    expect(await screen.findByText('Querying Prometheus for checkout-service')).toBeInTheDocument()
    expect(await screen.findByText('Upstream TLS certificate expired')).toBeInTheDocument()
  })

  it('reports the failure instead of inventing a root cause', async () => {
    installFetch(
      () =>
        new Response(JSON.stringify({ detail: 'Method Not Allowed' }), {
          status: 405,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    await openPlayground()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Could not run the investigation')
    expect(alert.textContent).toContain('405')
    // The fabricated script must not appear, on this path or any other.
    expect(screen.queryByText(/Database connection pool exhaustion/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Confidence: 87%/)).not.toBeInTheDocument()
  })

  it('surfaces a server-side error event rather than swallowing it', async () => {
    installFetch(() => sse([{ type: 'error', answer: 'No LLM provider configured.' }]))
    await openPlayground()

    expect(await screen.findByRole('alert')).toHaveTextContent('No LLM provider configured.')
  })

  it('lets the user continue after a real investigation, not only after 8 canned steps', async () => {
    installFetch(() => sse([{ type: 'trace', line: 'one step only' }, { type: 'done' }]))
    await openPlayground()

    expect(
      await screen.findByRole('button', { name: /connect your tools/i })
    ).toBeInTheDocument()
  })
})
