/**
 * Team Management Page
 * Manage team members and invite new users
 */

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  Users,
  UserPlus,
  Mail,
  Shield,
  Trash2,
  ChevronLeft,
  Crown,
  Eye,
  Edit3,
  AlertCircle,
  X
} from 'lucide-react'
import { useSupabaseAuth } from '../hooks/useSupabaseAuth'

interface TeamMember {
  id: string
  email: string
  name?: string
  role: 'owner' | 'admin' | 'member' | 'viewer'
  status: 'active' | 'pending' | 'invited'
  avatarUrl?: string
  joinedAt?: string
  lastActive?: string
}

interface Invitation {
  id: string
  email: string
  role: 'admin' | 'member' | 'viewer'
  createdAt: string
  expiresAt: string
}

interface TeamProps {
  onBack: () => void
}

export function Team({ onBack }: TeamProps) {
  const { user } = useSupabaseAuth()
  const [members, setMembers] = useState<TeamMember[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [loading, setLoading] = useState(true)
  const [showInviteModal, setShowInviteModal] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<'admin' | 'member' | 'viewer'>('member')
  const [inviting, setInviting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadTeam()
  }, [])

  const loadTeam = async () => {
    setLoading(true)
    try {
      // In production, fetch from API
      // For now, use demo data
      setMembers([
        {
          id: '1',
          email: user?.email || 'you@example.com',
          name: 'You',
          role: 'owner',
          status: 'active',
          avatarUrl: user?.user_metadata?.avatar_url as string | undefined,
          joinedAt: new Date().toISOString(),
          lastActive: new Date().toISOString()
        }
      ])
      setInvitations([])
    } catch {
      setError('Failed to load team members')
    } finally {
      setLoading(false)
    }
  }

  const handleInvite = async () => {
    if (!inviteEmail.trim()) return

    setInviting(true)
    setError(null)

    try {
      // In production, send invitation via API
      const newInvitation: Invitation = {
        id: Date.now().toString(),
        email: inviteEmail,
        role: inviteRole,
        createdAt: new Date().toISOString(),
        expiresAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString()
      }
      setInvitations([...invitations, newInvitation])
      setInviteEmail('')
      setShowInviteModal(false)
    } catch {
      setError('Failed to send invitation')
    } finally {
      setInviting(false)
    }
  }

  const handleRemoveMember = async (memberId: string) => {
    if (!confirm('Are you sure you want to remove this member?')) return

    try {
      setMembers(members.filter(m => m.id !== memberId))
    } catch {
      setError('Failed to remove member')
    }
  }

  const handleCancelInvitation = async (invitationId: string) => {
    try {
      setInvitations(invitations.filter(i => i.id !== invitationId))
    } catch {
      setError('Failed to cancel invitation')
    }
  }

  const getRoleIcon = (role: string) => {
    switch (role) {
      case 'owner': return <Crown size={14} style={{ color: '#f59e0b' }} />
      case 'admin': return <Shield size={14} style={{ color: 'var(--accent)' }} />
      case 'member': return <Edit3 size={14} style={{ color: 'var(--text-2)' }} />
      case 'viewer': return <Eye size={14} style={{ color: 'var(--text-2)' }} />
      default: return null
    }
  }

  const getRoleLabel = (role: string) => {
    switch (role) {
      case 'owner': return 'Owner'
      case 'admin': return 'Admin'
      case 'member': return 'Member'
      case 'viewer': return 'Viewer'
      default: return role
    }
  }

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--bg)' }}>
      {/* Header */}
      <header className="nav">
        <button
          className="nav__btn"
          onClick={onBack}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <ChevronLeft size={18} />
          <span>Back to Console</span>
        </button>
        <div className="nav__right">
          <span className="nav__tag">Team</span>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-3xl mx-auto px-4 py-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1
                className="text-2xl font-bold flex items-center gap-2"
                style={{ color: 'var(--text)' }}
              >
                <Users size={28} style={{ color: 'var(--accent)' }} />
                Team Members
              </h1>
              <p style={{ color: 'var(--text-2)' }}>
                Manage who has access to your workspace
              </p>
            </div>
            <button
              onClick={() => setShowInviteModal(true)}
              className="px-4 py-2 rounded-lg font-medium flex items-center gap-2"
              style={{
                backgroundColor: 'var(--accent)',
                color: 'var(--bg)'
              }}
            >
              <UserPlus size={18} />
              Invite Member
            </button>
          </div>

          {/* Error */}
          {error && (
            <div
              className="rounded-xl p-4 mb-6 flex items-center gap-3"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--bad)'
              }}
            >
              <AlertCircle size={20} style={{ color: 'var(--bad)' }} />
              <span style={{ color: 'var(--text)' }}>{error}</span>
              <button onClick={() => setError(null)} className="ml-auto">
                <X size={18} style={{ color: 'var(--text-2)' }} />
              </button>
            </div>
          )}

          {/* Members List */}
          <div
            className="rounded-xl overflow-hidden mb-6"
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--border)'
            }}
          >
            <div
              className="px-4 py-3"
              style={{ borderBottom: '1px solid var(--border)' }}
            >
              <h3 className="font-semibold" style={{ color: 'var(--text)' }}>
                Members ({members.length})
              </h3>
            </div>

            {loading ? (
              <div className="p-8 text-center" style={{ color: 'var(--text-2)' }}>
                Loading...
              </div>
            ) : (
              <div>
                {members.map((member, i) => (
                  <div
                    key={member.id}
                    className="px-4 py-3 flex items-center justify-between"
                    style={{
                      borderBottom: i < members.length - 1 ? '1px solid var(--border)' : 'none'
                    }}
                  >
                    <div className="flex items-center gap-3">
                      {member.avatarUrl ? (
                        <img
                          src={member.avatarUrl}
                          alt={member.name || member.email}
                          className="w-10 h-10 rounded-full"
                        />
                      ) : (
                        <div
                          className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-medium"
                          style={{
                            backgroundColor: 'var(--accent)',
                            color: 'var(--bg)'
                          }}
                        >
                          {(member.name || member.email).charAt(0).toUpperCase()}
                        </div>
                      )}
                      <div>
                        <p className="font-medium" style={{ color: 'var(--text)' }}>
                          {member.name || member.email}
                          {member.email === user?.email && (
                            <span className="ml-2 text-xs px-2 py-0.5 rounded" style={{ backgroundColor: 'var(--accent)', color: 'var(--bg)' }}>
                              You
                            </span>
                          )}
                        </p>
                        <p className="text-sm" style={{ color: 'var(--text-2)' }}>
                          {member.email}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-1.5">
                        {getRoleIcon(member.role)}
                        <span className="text-sm" style={{ color: 'var(--text-2)' }}>
                          {getRoleLabel(member.role)}
                        </span>
                      </div>
                      {member.role !== 'owner' && member.email !== user?.email && (
                        <button
                          onClick={() => handleRemoveMember(member.id)}
                          className="p-1.5 rounded-lg transition-colors"
                          style={{ color: 'var(--bad)' }}
                          title="Remove member"
                        >
                          <Trash2 size={16} />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Pending Invitations */}
          {invitations.length > 0 && (
            <div
              className="rounded-xl overflow-hidden"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--border)'
              }}
            >
              <div
                className="px-4 py-3"
                style={{ borderBottom: '1px solid var(--border)' }}
              >
                <h3 className="font-semibold" style={{ color: 'var(--text)' }}>
                  Pending Invitations ({invitations.length})
                </h3>
              </div>

              <div>
                {invitations.map((invitation, i) => (
                  <div
                    key={invitation.id}
                    className="px-4 py-3 flex items-center justify-between"
                    style={{
                      borderBottom: i < invitations.length - 1 ? '1px solid var(--border)' : 'none'
                    }}
                  >
                    <div className="flex items-center gap-3">
                      <Mail size={20} style={{ color: 'var(--text-2)' }} />
                      <div>
                        <p style={{ color: 'var(--text)' }}>{invitation.email}</p>
                        <p className="text-sm" style={{ color: 'var(--text-2)' }}>
                          Expires {new Date(invitation.expiresAt).toLocaleDateString()}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <span
                        className="text-sm px-2 py-1 rounded"
                        style={{
                          backgroundColor: 'var(--bg)',
                          color: 'var(--text-2)'
                        }}
                      >
                        {getRoleLabel(invitation.role)}
                      </span>
                      <button
                        onClick={() => handleCancelInvitation(invitation.id)}
                        className="text-sm"
                        style={{ color: 'var(--bad)' }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Role Permissions */}
          <div
            className="rounded-xl p-4 mt-6"
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--border)'
            }}
          >
            <h3
              className="font-semibold mb-3"
              style={{ color: 'var(--text)' }}
            >
              Role Permissions
            </h3>
            <div className="grid grid-cols-4 gap-4 text-sm">
              <div>
                <p className="font-medium flex items-center gap-1" style={{ color: '#f59e0b' }}>
                  <Crown size={14} /> Owner
                </p>
                <p style={{ color: 'var(--text-2)' }}>Full access, billing</p>
              </div>
              <div>
                <p className="font-medium flex items-center gap-1" style={{ color: 'var(--accent)' }}>
                  <Shield size={14} /> Admin
                </p>
                <p style={{ color: 'var(--text-2)' }}>Manage team, settings</p>
              </div>
              <div>
                <p className="font-medium flex items-center gap-1" style={{ color: 'var(--text)' }}>
                  <Edit3 size={14} /> Member
                </p>
                <p style={{ color: 'var(--text-2)' }}>Run investigations</p>
              </div>
              <div>
                <p className="font-medium flex items-center gap-1" style={{ color: 'var(--text)' }}>
                  <Eye size={14} /> Viewer
                </p>
                <p style={{ color: 'var(--text-2)' }}>View only</p>
              </div>
            </div>
          </div>
        </motion.div>
      </main>

      {/* Invite Modal */}
      {showInviteModal && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
          onClick={() => setShowInviteModal(false)}
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            className="w-full max-w-md rounded-xl p-6"
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--border)'
            }}
            onClick={e => e.stopPropagation()}
          >
            <h2
              className="text-lg font-semibold mb-4 flex items-center gap-2"
              style={{ color: 'var(--text)' }}
            >
              <UserPlus size={20} style={{ color: 'var(--accent)' }} />
              Invite Team Member
            </h2>

            <div className="space-y-4">
              <div>
                <label className="block text-sm mb-1" style={{ color: 'var(--text-2)' }}>
                  Email Address
                </label>
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={e => setInviteEmail(e.target.value)}
                  placeholder="colleague@company.com"
                  className="w-full px-3 py-2 rounded-lg"
                  style={{
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--border)',
                    color: 'var(--text)'
                  }}
                />
              </div>

              <div>
                <label className="block text-sm mb-1" style={{ color: 'var(--text-2)' }}>
                  Role
                </label>
                <select
                  value={inviteRole}
                  onChange={e => setInviteRole(e.target.value as 'admin' | 'member' | 'viewer')}
                  className="w-full px-3 py-2 rounded-lg"
                  style={{
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--border)',
                    color: 'var(--text)'
                  }}
                >
                  <option value="viewer">Viewer - View investigations only</option>
                  <option value="member">Member - Run investigations</option>
                  <option value="admin">Admin - Manage team & settings</option>
                </select>
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  onClick={() => setShowInviteModal(false)}
                  className="flex-1 px-4 py-2 rounded-lg font-medium"
                  style={{
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--border)',
                    color: 'var(--text)'
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleInvite}
                  disabled={!inviteEmail.trim() || inviting}
                  className="flex-1 px-4 py-2 rounded-lg font-medium"
                  style={{
                    backgroundColor: 'var(--accent)',
                    color: 'var(--bg)',
                    opacity: !inviteEmail.trim() || inviting ? 0.5 : 1
                  }}
                >
                  {inviting ? 'Sending...' : 'Send Invitation'}
                </button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </div>
  )
}
