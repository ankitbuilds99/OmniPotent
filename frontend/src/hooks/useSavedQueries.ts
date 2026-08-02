/**
 * Hook for managing saved investigation queries
 * Stores queries in localStorage for persistence
 */

import { useState, useEffect, useCallback } from 'react'

export interface SavedQuery {
  id: string
  text: string
  label?: string
  createdAt: string
  usedCount: number
  lastUsedAt?: string
}

const STORAGE_KEY = 'omnipotent-saved-queries'
const MAX_QUERIES = 20

function generateId(): string {
  return `sq_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function loadQueries(): SavedQuery[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      return JSON.parse(stored)
    }
  } catch (e) {
    console.warn('Failed to load saved queries:', e)
  }
  return []
}

function saveQueries(queries: SavedQuery[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(queries))
  } catch (e) {
    console.warn('Failed to save queries:', e)
  }
}

export function useSavedQueries() {
  const [queries, setQueries] = useState<SavedQuery[]>([])

  // Load on mount
  useEffect(() => {
    setQueries(loadQueries())
  }, [])

  // Save a new query
  const saveQuery = useCallback((text: string, label?: string): SavedQuery => {
    const trimmed = text.trim()

    // Check if already exists
    const existing = queries.find(q => q.text.toLowerCase() === trimmed.toLowerCase())
    if (existing) {
      // Update usage count
      const updated = queries.map(q =>
        q.id === existing.id
          ? { ...q, usedCount: q.usedCount + 1, lastUsedAt: new Date().toISOString() }
          : q
      )
      setQueries(updated)
      saveQueries(updated)
      return existing
    }

    // Create new
    const newQuery: SavedQuery = {
      id: generateId(),
      text: trimmed,
      label: label || undefined,
      createdAt: new Date().toISOString(),
      usedCount: 1,
      lastUsedAt: new Date().toISOString()
    }

    // Add and limit
    let updated = [newQuery, ...queries]
    if (updated.length > MAX_QUERIES) {
      updated = updated.slice(0, MAX_QUERIES)
    }

    setQueries(updated)
    saveQueries(updated)
    return newQuery
  }, [queries])

  // Remove a query
  const removeQuery = useCallback((id: string): void => {
    const updated = queries.filter(q => q.id !== id)
    setQueries(updated)
    saveQueries(updated)
  }, [queries])

  // Update a query label
  const updateQueryLabel = useCallback((id: string, label: string): void => {
    const updated = queries.map(q =>
      q.id === id ? { ...q, label } : q
    )
    setQueries(updated)
    saveQueries(updated)
  }, [queries])

  // Record query usage
  const recordUsage = useCallback((id: string): void => {
    const updated = queries.map(q =>
      q.id === id
        ? { ...q, usedCount: q.usedCount + 1, lastUsedAt: new Date().toISOString() }
        : q
    )
    setQueries(updated)
    saveQueries(updated)
  }, [queries])

  // Clear all queries
  const clearAll = useCallback((): void => {
    setQueries([])
    saveQueries([])
  }, [])

  // Get most used queries
  const mostUsed = queries
    .filter(q => q.usedCount > 1)
    .sort((a, b) => b.usedCount - a.usedCount)
    .slice(0, 5)

  // Get recent queries
  const recent = [...queries]
    .sort((a, b) => {
      const aTime = a.lastUsedAt || a.createdAt
      const bTime = b.lastUsedAt || b.createdAt
      return new Date(bTime).getTime() - new Date(aTime).getTime()
    })
    .slice(0, 5)

  return {
    queries,
    saveQuery,
    removeQuery,
    updateQueryLabel,
    recordUsage,
    clearAll,
    mostUsed,
    recent,
    count: queries.length
  }
}
