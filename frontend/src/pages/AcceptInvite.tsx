/**
 * Redeem a team invitation.
 *
 * Team.tsx mails a link to `/invite/{id}`, but the SPA had no route for it and
 * nothing in the frontend ever called POST /api/teams/invitations/{id}/accept.
 * The invitee clicked the link, landed on the marketing page with no prompt to
 * accept, and the invitation sat pending until it expired seven days later.
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CheckCircle, AlertCircle, Loader2, ChevronLeft } from 'lucide-react'

import { apiFetch, describeFailure } from '../lib/apiClient'
import { getAuthToken } from '../lib/authToken'

/** Where AppRoutes looks after a login to send the user back to their invite. */
export const PENDING_INVITATION_KEY = 'pending_invitation'

interface AcceptResponse {
  message: string
  team: { id: string | null; name: string | null }
}

type Phase = 'accepting' | 'accepted' | 'failed' | 'signin-required'

export function AcceptInvite() {
  const navigate = useNavigate()
  const { invitationId } = useParams<{ invitationId: string }>()

  const [phase, setPhase] = useState<Phase>('accepting')
  const [teamName, setTeamName] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const accept = useCallback(async () => {
    if (!invitationId) {
      setPhase('failed')
      setError('This invitation link is missing its invitation id.')
      return
    }

    // The endpoint checks that the caller's email matches the address the
    // invitation was issued to, so it needs a signed-in user. Remember where
    // to come back to before handing them off to the login page.
    if (!getAuthToken()) {
      localStorage.setItem(PENDING_INVITATION_KEY, invitationId)
      setPhase('signin-required')
      return
    }

    setPhase('accepting')
    setError(null)
    try {
      const res = await apiFetch(`/api/teams/invitations/${invitationId}/accept`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error(await describeFailure(res))
      const body = (await res.json()) as AcceptResponse
      setTeamName(body.team?.name ?? null)
      setPhase('accepted')
      localStorage.removeItem(PENDING_INVITATION_KEY)
    } catch (err) {
      // 403 means the invitation was addressed to somebody else, 404 that it
      // has been withdrawn or has expired. The server says which; pass it on
      // rather than reducing every case to "something went wrong".
      setError(err instanceof Error ? err.message : String(err))
      setPhase('failed')
    }
  }, [invitationId])

  useEffect(() => {
    accept()
  }, [accept])

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--bg)' }}>
      <header className="nav">
        <button
          className="nav__btn"
          onClick={() => navigate('/')}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <ChevronLeft size={18} />
          <span>Home</span>
        </button>
        <div className="nav__right">
          <span className="nav__tag">Invitation</span>
        </div>
      </header>

      <main className="max-w-lg mx-auto px-4 py-16">
        <div
          className="rounded-xl p-8 text-center"
          style={{ backgroundColor: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          {phase === 'accepting' && (
            <>
              <Loader2
                size={40}
                className="mx-auto mb-4 animate-spin"
                style={{ color: 'var(--accent)' }}
              />
              <p style={{ color: 'var(--text-2)' }}>Accepting your invitation…</p>
            </>
          )}

          {phase === 'signin-required' && (
            <>
              <AlertCircle size={40} className="mx-auto mb-4" style={{ color: 'var(--accent)' }} />
              <h1 className="text-xl font-semibold mb-2" style={{ color: 'var(--text)' }}>
                Sign in to join
              </h1>
              <p className="mb-6" style={{ color: 'var(--text-2)' }}>
                Sign in with the address this invitation was sent to, and we will bring you
                straight back here.
              </p>
              <button
                onClick={() => navigate('/login')}
                className="px-4 py-2 rounded-lg font-medium"
                style={{ backgroundColor: 'var(--accent)', color: 'var(--bg)' }}
              >
                Sign in
              </button>
            </>
          )}

          {phase === 'accepted' && (
            <>
              <CheckCircle size={40} className="mx-auto mb-4" style={{ color: 'var(--good)' }} />
              <h1 className="text-xl font-semibold mb-2" style={{ color: 'var(--text)' }}>
                {teamName ? `You have joined ${teamName}` : 'Invitation accepted'}
              </h1>
              <p className="mb-6" style={{ color: 'var(--text-2)' }}>
                You now have access to this team's incidents, runbooks and on-call schedules.
              </p>
              <button
                onClick={() => navigate('/team')}
                className="px-4 py-2 rounded-lg font-medium"
                style={{ backgroundColor: 'var(--accent)', color: 'var(--bg)' }}
              >
                Go to the team
              </button>
            </>
          )}

          {phase === 'failed' && (
            <>
              <AlertCircle size={40} className="mx-auto mb-4" style={{ color: 'var(--bad)' }} />
              <h1 className="text-xl font-semibold mb-2" style={{ color: 'var(--text)' }}>
                This invitation could not be accepted
              </h1>
              <p role="alert" className="mb-6" style={{ color: 'var(--text-2)' }}>
                {error}
              </p>
              <div className="flex items-center justify-center gap-3">
                <button
                  onClick={accept}
                  className="px-4 py-2 rounded-lg font-medium"
                  style={{ backgroundColor: 'var(--accent)', color: 'var(--bg)' }}
                >
                  Try again
                </button>
                <button
                  onClick={() => navigate('/console')}
                  className="px-4 py-2 rounded-lg font-medium"
                  style={{
                    backgroundColor: 'var(--surface)',
                    border: '1px solid var(--border)',
                    color: 'var(--text)',
                  }}
                >
                  Go to the console
                </button>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
