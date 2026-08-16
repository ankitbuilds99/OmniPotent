import emailjs from '@emailjs/browser'

const SERVICE_ID = import.meta.env.VITE_EMAILJS_SERVICE_ID || 'service_c97omni'
const TEMPLATE_ID = import.meta.env.VITE_EMAILJS_TEMPLATE_ID || 'template_jeqk7kd'
const PUBLIC_KEY = import.meta.env.VITE_EMAILJS_PUBLIC_KEY || ''

interface InvitationEmailParams {
  toEmail: string
  inviterName: string
  inviterEmail: string
  role: string
  inviteLink: string
}

export async function sendInvitationEmail(params: InvitationEmailParams): Promise<boolean> {
  if (!PUBLIC_KEY) {
    console.warn('EmailJS public key not configured - email not sent')
    return false
  }

  try {
    const templateParams = {
      to_email: params.toEmail,
      inviter_name: params.inviterName,
      inviter_email: params.inviterEmail,
      role: params.role,
      invite_link: params.inviteLink,
      app_name: 'OmniPotent'
    }

    const response = await emailjs.send(
      SERVICE_ID,
      TEMPLATE_ID,
      templateParams,
      PUBLIC_KEY
    )

    if (response.status === 200) {
      console.log('Invitation email sent successfully')
      return true
    }

    console.error('EmailJS returned non-200 status:', response)
    return false
  } catch (error) {
    console.error('Failed to send invitation email:', error)
    return false
  }
}

export function isEmailServiceConfigured(): boolean {
  return Boolean(PUBLIC_KEY)
}
