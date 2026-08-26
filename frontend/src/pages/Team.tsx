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
  X,
  CheckCircle
} from 'lucide-react'
import { useSupabaseAuth } from '../hooks/useSupabaseAuth'
import { apiFetch } from '../lib/apiClient'
import { sendInvitationEmail, isEmailServiceConfigured } from '../lib/emailService'

type Role = 'owner' | 'admin' | 'member' | 'viewer'

/** Shape of GET /api/teams/{id}/members (app/teams/models.py TeamMemberResponse). */
interface TeamMember {
  id: string
  /** The id the member routes are keyed by — NOT the membership id. */
  user_id: string
  email: string
  name?: string | null
  role: Role
  avatar_url?: string | null
  joined_at?: string
  last_active?: string | null
}

/** Shape of GET/POST /api/teams/{id}/invitations (InvitationResponse). */
interface Invitation {
  id: string
  email: string
  role: Role
  status?: string
  created_at: string
  expires_at: string
}

/** Shape of GET /api/teams (TeamResponse). */
interface TeamSummary {
  id: string
  name: string
  slug: string
  owner_id: string
  member_count: number
  role: Role | null
}

/** Pull the server's error detail out of a failed response so the user sees the
 *  real reason ("Admins cannot invite as admin or owner") instead of a generic
 *  message — or nothing at all, which is what happened before. */
async function errorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json()
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail) return detail
  } catch {
    // Non-JSON body; fall through.
  }
  return `${fallback} (HTTP ${res.status})`
}

interface TeamProps {
  onBack: () => void
}

export function Team({ onBack }: TeamProps) {
  const { user } = useSupabaseAuth()
  const [team, setTeam] = useState<TeamSummary | null>(null)
  const [members, setMembers] = useState<TeamMember[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [loading, setLoading] = useState(true)
  const [showInviteModal, setShowInviteModal] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<'admin' | 'member' | 'viewer'>('member')
  const [inviting, setInviting] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newTeamName, setNewTeamName] = useState('')
  const [busyMember, setBusyMember] = useState<string | null>(null)
  const [currentEmail, setCurrentEmail] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    loadTeam()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    setCurrentEmail(user?.email ?? '')
  }, [user?.email])

  // Who "You" is. Supabase logins know it locally; an OAuth session token does
  // not, so ask the backend rather than guessing.
  useEffect(() => {
    if (user?.email) return
    let cancelled = false
    apiFetch('/auth/me')
      .then(res => (res.ok ? res.json() : null))
      .then(me => {
        if (!cancelled && me?.email) setCurrentEmail(me.email as string)
      })
      .catch(() => {
        /* Not signed in through the backend OAuth flow; leave it blank. */
      })
    return () => {
      cancelled = true
    }
  }, [user?.email])

  /** Load members + invitations for a team. Invitations are admin-only, so a 403
   *  there is expected for members/viewers and must not blank out the page. */
  const loadTeamDetail = async (teamId: string) => {
    const membersRes = await apiFetch(`/api/teams/${teamId}/members`)
    if (!membersRes.ok) {
      setMembers([])
      setError(await errorDetail(membersRes, 'Failed to load team members'))
      return
    }
    setMembers((await membersRes.json()) as TeamMember[])

    const invRes = await apiFetch(`/api/teams/${teamId}/invitations`)
    setInvitations(invRes.ok ? ((await invRes.json()) as Invitation[]) : [])
  }

  const loadTeam = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch('/api/teams')
      if (!res.ok) {
        setTeam(null)
        setMembers([])
        setInvitations([])
        setError(await errorDetail(res, 'Failed to load teams'))
        return
      }
      const teams = (await res.json()) as TeamSummary[]
      if (teams.length === 0) {
        // No team yet. Say so and offer to create one — never invent a member.
        setTeam(null)
        setMembers([])
        setInvitations([])
        return
      }
      setTeam(teams[0])
      await loadTeamDetail(teams[0].id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load team members')
      setTeam(null)
      setMembers([])
      setInvitations([])
    } finally {
      setLoading(false)
    }
  }

  const handleCreateTeam = async () => {
    if (!newTeamName.trim()) return
    setCreating(true)
    setError(null)
    try {
      const res = await apiFetch('/api/teams', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newTeamName.trim() })
      })
      if (!res.ok) {
        setError(await errorDetail(res, 'Failed to create team'))
        return
      }
      const created = (await res.json()) as TeamSummary
      setTeam(created)
      setNewTeamName('')
      await loadTeamDetail(created.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create team')
    } finally {
      setCreating(false)
    }
  }

  const handleInvite = async () => {
    if (!inviteEmail.trim() || !team) return

    setInviting(true)
    setError(null)
    setSuccess(null)

    try {
      const res = await apiFetch(`/api/teams/${team.id}/invitations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: inviteEmail.trim(), role: inviteRole })
      })
      if (!res.ok) {
        setError(await errorDetail(res, 'Failed to send invitation'))
        return
      }
      const created = (await res.json()) as Invitation
      setInvitations(prev => [...prev, created])
      setInviteEmail('')
      setShowInviteModal(false)

      // The invitation now exists server-side; the backend records it but does not
      // mail anyone (app/teams/router.py: "In production: send email notification
      // here"), so EmailJS delivers it from the browser. The link carries the id the
      // server issued — a Date.now() id would address an invitation that does not
      // exist, so the accept route would 404 on every click.
      const inviteLink = `${window.location.origin}/invite/${created.id}`
      const emailSent = await sendInvitationEmail({
        toEmail: created.email,
        inviterName:
          (user?.user_metadata?.full_name as string) ||
          user?.email ||
          currentEmail ||
          'Team Admin',
        inviterEmail: user?.email || currentEmail || '',
        role: created.role,
        inviteLink
      })

      // The record and the email are two separate things that can each fail, and
      // the invitee only hears about the second one. Say which half happened.
      if (emailSent) {
        setSuccess(`Invitation sent to ${created.email}`)
      } else if (!isEmailServiceConfigured()) {
        setSuccess(`Invitation created for ${created.email} (email not configured)`)
      } else {
        setError('Invitation created but email failed to send')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to send invitation')
    } finally {
      setInviting(false)
    }
  }

  const handleChangeRole = async (userId: string, role: Role) => {
    if (!team) return
    setBusyMember(userId)
    setError(null)
    try {
      const res = await apiFetch(`/api/teams/${team.id}/members/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role })
      })
      if (!res.ok) {
        setError(await errorDetail(res, 'Failed to change role'))
        return
      }
      const updated = (await res.json()) as TeamMember
      setMembers(prev => prev.map(m => (m.user_id === userId ? updated : m)))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change role')
    } finally {
      setBusyMember(null)
    }
  }

  const handleRemoveMember = async (userId: string) => {
    if (!team) return
    if (!confirm('Are you sure you want to remove this member?')) return

    setBusyMember(userId)
    setError(null)
    try {
      const res = await apiFetch(`/api/teams/${team.id}/members/${userId}`, {
        method: 'DELETE'
      })
      if (!res.ok) {
        setError(await errorDetail(res, 'Failed to remove member'))
        return
      }
      setMembers(prev => prev.filter(m => m.user_id !== userId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to remove member')
    } finally {
      setBusyMember(null)
    }
  }

  const handleCancelInvitation = async (invitationId: string) => {
    if (!team) return
    setError(null)
    try {
      const res = await apiFetch(`/api/teams/${team.id}/invitations/${invitationId}`, {
        method: 'DELETE'
      })
      if (!res.ok) {
        setError(await errorDetail(res, 'Failed to cancel invitation'))
        return
      }
      setInvitations(prev => prev.filter(i => i.id !== invitationId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to cancel invitation')
    }
  }

  const canManage = team?.role === 'owner' || team?.role === 'admin'

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
              disabled={!team || !canManage}
              title={
                !team
                  ? 'Create a team first'
                  : canManage
                    ? 'Invite a member'
                    : 'Only owners and admins can invite'
              }
              className="px-4 py-2 rounded-lg font-medium flex items-center gap-2"
              style={{
                backgroundColor: 'var(--accent)',
                color: 'var(--bg)',
                opacity: !team || !canManage ? 0.5 : 1
              }}
            >
              <UserPlus size={18} />
              Invite Member
            </button>
          </div>

          {/* Success */}
          {success && (
            <div
              className="rounded-xl p-4 mb-6 flex items-center gap-3"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--good)'
              }}
            >
              <CheckCircle size={20} style={{ color: 'var(--good)' }} />
              <span style={{ color: 'var(--text)' }}>{success}</span>
              <button onClick={() => setSuccess(null)} className="ml-auto">
                <X size={18} style={{ color: 'var(--text-2)' }} />
              </button>
            </div>
          )}

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
            ) : !team ? (
              <div className="p-8 text-center space-y-3" style={{ color: 'var(--text-2)' }}>
                <p>You do not belong to a team yet.</p>
                <div className="flex items-center gap-2 justify-center">
                  <input
                    aria-label="New team name"
                    value={newTeamName}
                    onChange={e => setNewTeamName(e.target.value)}
                    placeholder="Team name"
                    className="px-3 py-2 rounded-lg"
                    style={{
                      backgroundColor: 'var(--bg)',
                      border: '1px solid var(--border)',
                      color: 'var(--text)'
                    }}
                  />
                  <button
                    onClick={handleCreateTeam}
                    disabled={!newTeamName.trim() || creating}
                    className="px-4 py-2 rounded-lg font-medium"
                    style={{
                      backgroundColor: 'var(--accent)',
                      color: 'var(--bg)',
                      opacity: !newTeamName.trim() || creating ? 0.5 : 1
                    }}
                  >
                    {creating ? 'Creating...' : 'Create team'}
                  </button>
                </div>
              </div>
            ) : (
              <div>
                {members.map((member, i) => (
                  <div
                    key={member.user_id}
                    className="px-4 py-3 flex items-center justify-between"
                    style={{
                      borderBottom: i < members.length - 1 ? '1px solid var(--border)' : 'none'
                    }}
                  >
                    <div className="flex items-center gap-3">
                      {member.avatar_url ? (
                        <img
                          src={member.avatar_url}
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
                          {currentEmail !== '' && member.email === currentEmail && (
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
                        {canManage && member.role !== 'owner' ? (
                          <select
                            aria-label={`Role for ${member.email}`}
                            value={member.role}
                            disabled={busyMember === member.user_id}
                            onChange={e =>
                              handleChangeRole(member.user_id, e.target.value as Role)
                            }
                            className="text-sm px-2 py-1 rounded"
                            style={{
                              backgroundColor: 'var(--bg)',
                              border: '1px solid var(--border)',
                              color: 'var(--text)'
                            }}
                          >
                            <option value="viewer">Viewer</option>
                            <option value="member">Member</option>
                            <option value="admin">Admin</option>
                          </select>
                        ) : (
                          <span className="text-sm" style={{ color: 'var(--text-2)' }}>
                            {getRoleLabel(member.role)}
                          </span>
                        )}
                      </div>
                      {member.role !== 'owner' && member.email !== currentEmail && (
                        <button
                          onClick={() => handleRemoveMember(member.user_id)}
                          disabled={busyMember === member.user_id}
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
                          Expires {new Date(invitation.expires_at).toLocaleDateString()}
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
