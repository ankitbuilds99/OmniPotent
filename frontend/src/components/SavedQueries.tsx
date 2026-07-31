/**
 * Saved Queries Panel Component
 * Shows bookmarked investigation prompts
 */

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Bookmark,
  X,
  Clock,
  TrendingUp,
  Trash2,
  Edit2,
  Check
} from 'lucide-react'
import { useSavedQueries, SavedQuery } from '../hooks/useSavedQueries'

interface SavedQueriesPanelProps {
  onSelect: (query: string) => void
  onClose: () => void
}

export function SavedQueriesPanel({ onSelect, onClose }: SavedQueriesPanelProps) {
  const {
    queries,
    removeQuery,
    updateQueryLabel,
    recordUsage,
    clearAll,
    mostUsed,
    recent
  } = useSavedQueries()

  const [activeTab, setActiveTab] = useState<'all' | 'recent' | 'popular'>('recent')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editLabel, setEditLabel] = useState('')

  const handleSelect = (query: SavedQuery) => {
    recordUsage(query.id)
    onSelect(query.text)
    onClose()
  }

  const handleEdit = (query: SavedQuery) => {
    setEditingId(query.id)
    setEditLabel(query.label || '')
  }

  const handleSaveLabel = (id: string) => {
    updateQueryLabel(id, editLabel)
    setEditingId(null)
    setEditLabel('')
  }

  const displayQueries = activeTab === 'recent' ? recent
    : activeTab === 'popular' ? mostUsed
    : queries

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="w-full max-w-lg rounded-xl overflow-hidden"
        style={{
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--border)'
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between p-4"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <h2
            className="text-lg font-semibold flex items-center gap-2"
            style={{ color: 'var(--text)' }}
          >
            <Bookmark size={20} style={{ color: 'var(--accent)' }} />
            Saved Queries
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded-lg transition-colors"
            style={{ color: 'var(--text-2)' }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Tabs */}
        <div
          className="flex gap-1 p-2"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          {[
            { id: 'recent', label: 'Recent', icon: Clock },
            { id: 'popular', label: 'Most Used', icon: TrendingUp },
            { id: 'all', label: 'All', icon: Bookmark }
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as 'all' | 'recent' | 'popular')}
              className="flex-1 px-3 py-2 rounded-lg text-sm font-medium flex items-center justify-center gap-1.5 transition-colors"
              style={{
                backgroundColor: activeTab === tab.id ? 'var(--accent)' : 'transparent',
                color: activeTab === tab.id ? 'var(--bg)' : 'var(--text-2)'
              }}
            >
              <tab.icon size={14} />
              {tab.label}
            </button>
          ))}
        </div>

        {/* Query List */}
        <div className="max-h-80 overflow-y-auto p-2">
          {displayQueries.length === 0 ? (
            <div className="text-center py-8">
              <Bookmark
                size={32}
                className="mx-auto mb-3"
                style={{ color: 'var(--text-2)' }}
              />
              <p style={{ color: 'var(--text-2)' }}>
                {activeTab === 'popular'
                  ? 'No frequently used queries yet'
                  : 'No saved queries yet'}
              </p>
              <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
                Click the bookmark icon when composing to save
              </p>
            </div>
          ) : (
            <AnimatePresence mode="popLayout">
              {displayQueries.map(query => (
                <motion.div
                  key={query.id}
                  layout
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, x: -10 }}
                  className="group p-3 rounded-lg mb-2 cursor-pointer transition-colors"
                  style={{
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--border)'
                  }}
                  onClick={() => handleSelect(query)}
                >
                  {/* Label (editable) */}
                  {editingId === query.id ? (
                    <div className="flex items-center gap-2 mb-2">
                      <input
                        type="text"
                        value={editLabel}
                        onChange={e => setEditLabel(e.target.value)}
                        placeholder="Add a label..."
                        className="flex-1 px-2 py-1 rounded text-sm"
                        style={{
                          backgroundColor: 'var(--surface)',
                          border: '1px solid var(--accent)',
                          color: 'var(--text)'
                        }}
                        onClick={e => e.stopPropagation()}
                        autoFocus
                      />
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleSaveLabel(query.id)
                        }}
                        className="p-1 rounded"
                        style={{ color: 'var(--ok)' }}
                      >
                        <Check size={16} />
                      </button>
                    </div>
                  ) : query.label ? (
                    <p
                      className="text-xs font-medium mb-1"
                      style={{ color: 'var(--accent)' }}
                    >
                      {query.label}
                    </p>
                  ) : null}

                  {/* Query text */}
                  <p
                    className="text-sm line-clamp-2"
                    style={{ color: 'var(--text)' }}
                  >
                    {query.text}
                  </p>

                  {/* Meta & Actions */}
                  <div className="flex items-center justify-between mt-2">
                    <span
                      className="text-xs"
                      style={{ color: 'var(--text-2)' }}
                    >
                      Used {query.usedCount}x
                    </span>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleEdit(query)
                        }}
                        className="p-1 rounded transition-colors"
                        style={{ color: 'var(--text-2)' }}
                        title="Edit label"
                      >
                        <Edit2 size={14} />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          removeQuery(query.id)
                        }}
                        className="p-1 rounded transition-colors"
                        style={{ color: 'var(--bad)' }}
                        title="Remove"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </div>

        {/* Footer */}
        {queries.length > 0 && (
          <div
            className="flex items-center justify-between p-3"
            style={{ borderTop: '1px solid var(--border)' }}
          >
            <span
              className="text-sm"
              style={{ color: 'var(--text-2)' }}
            >
              {queries.length} saved queries
            </span>
            <button
              onClick={clearAll}
              className="text-sm px-3 py-1 rounded-lg transition-colors"
              style={{
                color: 'var(--bad)',
                backgroundColor: 'transparent'
              }}
            >
              Clear All
            </button>
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}
