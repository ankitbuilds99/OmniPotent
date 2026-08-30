import { useState, useEffect } from 'react'

import { apiFetch } from '../lib/apiClient'

export interface Catalog {
  servers: number
  tools: number
  labels: string[]
}

/**
 * Connector and tool counts, counted rather than typed in.
 *
 * Extracted from Metrics.tsx so the hero's stat row can stop hardcoding the
 * same numbers Metrics already fixed. Those cards read "48 AI Tools" and
 * "9 MCP Servers" as literals. Both were wrong: MCP_CATALOG (app/api/main.py)
 * carries 11 servers exposing 64 tools, and had done since traces, deploys,
 * argocd and terraform were added. Nobody updated the landing page, because
 * nothing connected the two — the numbers were only ever going to drift
 * further, and understate the product while they did.
 *
 * GET /config returns that catalog verbatim and needs no credential, so the
 * cards can just count it. A build that adds a connector now says so by itself.
 */
export function useCatalog(): Catalog | null {
  const [catalog, setCatalog] = useState<Catalog | null>(null)

  useEffect(() => {
    let cancelled = false

    apiFetch('/config')
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((cfg: { servers?: { label?: string; tools?: string[] }[] }) => {
        if (cancelled) return
        const servers = cfg.servers ?? []
        setCatalog({
          servers: servers.length,
          tools: servers.reduce((n, s) => n + (s.tools?.length ?? 0), 0),
          labels: servers.map((s) => s.label ?? '').filter(Boolean)
        })
      })
      .catch(() => {
        // A marketing page has no business showing an error banner, and a
        // stale literal is exactly what this replaced — so the derived
        // cards simply do not render. Better a shorter row than a made-up one.
      })

    return () => {
      cancelled = true
    }
  }, [])

  return catalog
}
