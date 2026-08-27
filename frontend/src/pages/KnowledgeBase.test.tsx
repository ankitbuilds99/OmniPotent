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

// The edit pencil's only implementation was `onEdit={() => setSelectedArticle(null)}`
// — it closed the modal and issued no request, though PUT /api/knowledge/
// documents/{doc_id} had been there all along. An operator who spotted a wrong
// command in a runbook could only delete the document and write it again.
describe('KnowledgeBase editing', () => {
  beforeEach(() => {
    calls = []
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  let bodies: Record<string, unknown>[] = []

  function installEditFetch(putReply: () => Response = () => json({ message: 'Document updated' })) {
    bodies = []
    let stored = { ...ARTICLE }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = (init?.method ?? 'GET').toUpperCase()
        calls.push({ url, method })
        if (init?.body) bodies.push(JSON.parse(String(init.body)))
        if (method === 'PUT') {
          const res = putReply()
          if (res.ok) stored = { ...stored, ...(JSON.parse(String(init!.body)) as object) }
          return res
        }
        if (method === 'GET' && url === '/api/knowledge/documents/doc-1') return json(stored)
        return json({ documents: [stored], total: 1, limit: 50, offset: 0 })
      })
    )
  }

  async function openEditor() {
    render(<KnowledgeBase onBack={() => {}} />)
    fireEvent.click(await screen.findByText('Restarting the settlement worker'))
    fireEvent.click(await screen.findByLabelText('Edit article'))
  }

  it('PUTs the rewritten article and shows what the server kept', async () => {
    installEditFetch()
    await openEditor()

    fireEvent.change(screen.getByLabelText('Content'), {
      target: { value: 'Scale the deployment to zero, wait 30s, then back up.' },
    })
    fireEvent.change(screen.getByLabelText('Tags'), { target: { value: 'payments, settlement' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() =>
      expect(
        screen.getByText('Scale the deployment to zero, wait 30s, then back up.')
      ).toBeInTheDocument()
    )
    const put = calls.find(c => c.method === 'PUT')
    expect(put?.url).toBe('/api/knowledge/documents/doc-1')
    expect(bodies[0]).toEqual({
      title: 'Restarting the settlement worker',
      content: 'Scale the deployment to zero, wait 30s, then back up.',
      tags: ['payments', 'settlement'],
      service: 'checkout-api',
    })
    // PUT answers only {"message": ...}, so the page re-reads the document.
    expect(calls.filter(c => c.method === 'GET' && c.url === '/api/knowledge/documents/doc-1'))
      .toHaveLength(1)
  })

  it('keeps the rewrite in the editor when the save fails', async () => {
    installEditFetch(() => json({ detail: 'Document not found' }, 404))
    await openEditor()

    fireEvent.change(screen.getByLabelText('Content'), { target: { value: 'A careful rewrite.' } })
    fireEvent.click(screen.getByText('Save'))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Document not found/)
    // Still in the editor, with the operator's only copy of the text in it.
    expect(screen.getByLabelText('Content')).toHaveValue('A careful rewrite.')
  })

  it('will not save an article with no title', async () => {
    installEditFetch()
    await openEditor()

    fireEvent.change(screen.getByLabelText('Title'), { target: { value: '   ' } })
    fireEvent.click(screen.getByText('Save'))

    expect(calls.some(c => c.method === 'PUT')).toBe(false)
  })

  it('does not close the article when the pencil is clicked', async () => {
    installEditFetch()
    await openEditor()

    // The old behaviour: the modal vanished and nothing else happened.
    expect(screen.getByLabelText('Content')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()
  })
})

// The header reported `articles.length` while the API caps a page at 50, and
// the request carried neither limit nor offset. A base of 55 documents said
// "50 articles" and the other five were unreachable — the same truncation hit
// searches, so a runbook on page 2 read as one that does not exist.
describe('KnowledgeBase paging', () => {
  beforeEach(() => {
    calls = []
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  /** 55 documents, served 50 at a time, the way the backend does it. */
  function installPagedFetch(totalDocs = 55) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), 'http://x')
        calls.push({ url: String(input), method: (init?.method ?? 'GET').toUpperCase() })
        const limit = Number(url.searchParams.get('limit') ?? 50)
        const offset = Number(url.searchParams.get('offset') ?? 0)
        const documents = Array.from({ length: Math.max(0, Math.min(limit, totalDocs - offset)) }, (_, i) => ({
          ...ARTICLE,
          id: `doc-${offset + i}`,
          title: `Article ${offset + i}`,
        }))
        return json({ documents, total: totalDocs, limit, offset })
      })
    )
  }

  it('reports the server\'s total, not the size of one page', async () => {
    installPagedFetch()
    render(<KnowledgeBase onBack={() => {}} />)

    expect(await screen.findByText('55 articles')).toBeInTheDocument()
    expect(screen.queryByText('50 articles')).not.toBeInTheDocument()
  })

  it('asks for a page and can reach the documents past the first 50', async () => {
    installPagedFetch()
    render(<KnowledgeBase onBack={() => {}} />)
    await screen.findByText('Article 0')
    expect(screen.queryByText('Article 50')).not.toBeInTheDocument()
    expect(screen.getByText('Showing 1–50 of 55')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Next'))

    expect(await screen.findByText('Article 50')).toBeInTheDocument()
    expect(await screen.findByText('Showing 51–55 of 55')).toBeInTheDocument()
    expect(screen.getByText('Next')).toBeDisabled()
    const last = calls[calls.length - 1]
    expect(new URL(last.url, 'http://x').searchParams.get('offset')).toBe('50')
  })

  it('goes back to page one, and starts a new search there', async () => {
    installPagedFetch()
    render(<KnowledgeBase onBack={() => {}} />)
    await screen.findByText('Article 0')
    fireEvent.click(screen.getByText('Next'))
    await screen.findByText('Article 50')

    fireEvent.click(screen.getByText('Previous'))
    expect(await screen.findByText('Article 0')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Next'))
    await screen.findByText('Article 50')
    // A new search is a new result set; page 2 of the old one would read as
    // "no matches" for a search that has plenty.
    fireEvent.change(screen.getByPlaceholderText('Search articles...'), {
      target: { value: 'settlement' },
    })

    await waitFor(() => {
      const last = calls[calls.length - 1]
      const params = new URL(last.url, 'http://x').searchParams
      expect(params.get('search')).toBe('settlement')
      expect(params.get('offset')).toBe('0')
    })
  })

  it('offers no paging when everything fits on one page', async () => {
    installPagedFetch(3)
    render(<KnowledgeBase onBack={() => {}} />)

    expect(await screen.findByText('3 articles')).toBeInTheDocument()
    expect(screen.queryByText('Next')).not.toBeInTheDocument()
  })
})
