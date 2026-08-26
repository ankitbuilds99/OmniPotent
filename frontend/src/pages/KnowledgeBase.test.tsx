// `fetchArticles` was `if (res.ok) { ... }` with no else, so a 401, a 403 from
// the feature gate or a 500 left `articles` at its previous value — [] on first
// load — and the page rendered "No articles found / Try a different search or
// category". An operator searching the knowledge base during an incident was
// told the runbook does not exist when the truth was that their session had
// expired. Delete had the same shape: the row was removed from local state only
// `if (res.ok)`, and a failure said nothing at all.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { KnowledgeBase } from './KnowledgeBase'

interface Call {
  url: string
  method: string
}

let calls: Call[] = []

const ARTICLE = {
  id: 'doc-1',
  title: 'Restarting the settlement worker',
  content: 'Scale the deployment to zero, then back up.',
  doc_type: 'runbook',
  tags: ['payments'],
  service: 'checkout-api',
  author: 'alice',
  created_at: '2026-01-02T03:04:05+00:00',
  updated_at: '2026-01-02T03:04:05+00:00',
  views: 0,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(reply: (key: string) => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })
      return reply(`${method} ${url.split('?')[0]}`)
    })
  )
}

describe('KnowledgeBase', () => {
  beforeEach(() => {
    calls = []
    localStorage.clear()
    vi.stubGlobal('confirm', () => true)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists what the API returned', async () => {
    installFetch(() => json({ documents: [ARTICLE] }))
    render(<KnowledgeBase onBack={() => {}} />)

    expect(await screen.findByText('Restarting the settlement worker')).toBeInTheDocument()
  })

  it('says the load failed instead of claiming the knowledge base is empty', async () => {
    installFetch(() => json({ detail: 'Not authenticated' }, 401))
    render(<KnowledgeBase onBack={() => {}} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/Not authenticated/)
    expect(screen.queryByText('No articles found')).not.toBeInTheDocument()
    expect(screen.queryByText('Try a different search or category')).not.toBeInTheDocument()
  })

  it('flattens a 422 field list rather than printing [object Object]', async () => {
    installFetch(() =>
      json({ detail: [{ loc: ['query', 'doc_type'], msg: 'value is not a valid enumeration member' }] }, 422)
    )
    render(<KnowledgeBase onBack={() => {}} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/query\.doc_type/)
    expect(alert).not.toHaveTextContent(/object Object/)
  })

  it('retries the load from the error banner', async () => {
    let failed = false
    installFetch(() => {
      if (!failed) {
        failed = true
        return json({ detail: 'Upstream unavailable' }, 503)
      }
      return json({ documents: [ARTICLE] })
    })
    render(<KnowledgeBase onBack={() => {}} />)

    await screen.findByRole('alert')
    fireEvent.click(screen.getByText('Retry'))

    expect(await screen.findByText('Restarting the settlement worker')).toBeInTheDocument()
  })

  it('reports a failed delete instead of leaving the row and saying nothing', async () => {
    installFetch(key => {
      if (key.startsWith('DELETE ')) return json({ detail: 'Document not found' }, 404)
      return json({ documents: [ARTICLE] })
    })
    render(<KnowledgeBase onBack={() => {}} />)
    await screen.findByText('Restarting the settlement worker')

    fireEvent.click(screen.getByLabelText('Delete Restarting the settlement worker'))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/Document not found/)
    })
    // Still on screen — it was not deleted, and the page no longer pretends it was.
    expect(screen.getByText('Restarting the settlement worker')).toBeInTheDocument()
  })

  it('removes the row only when the delete actually succeeded', async () => {
    installFetch(key => {
      if (key.startsWith('DELETE ')) return json({ status: 'deleted' })
      return json({ documents: [ARTICLE] })
    })
    render(<KnowledgeBase onBack={() => {}} />)
    await screen.findByText('Restarting the settlement worker')

    fireEvent.click(screen.getByLabelText('Delete Restarting the settlement worker'))

    await waitFor(() => {
      expect(screen.queryByText('Restarting the settlement worker')).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(calls).toContainEqual({ url: '/api/knowledge/documents/doc-1', method: 'DELETE' })
  })
})
