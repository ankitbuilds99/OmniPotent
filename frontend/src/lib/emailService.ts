import emailjs from '@emailjs/browser'

// De-faked: SERVICE_ID and TEMPLATE_ID used to default to the developer's
// personal EmailJS account ('service_c97omni' / 'template_jeqk7kd'), so any
// deployment that set only VITE_EMAILJS_PUBLIC_KEY silently routed its
// invitation mail through that account. All three now default to unset, and
// no mail is attempted unless all three are explicitly configured.
const SERVICE_ID = import.meta.env.VITE_EMAILJS_SERVICE_ID || ''
const TEMPLATE_ID = import.meta.env.VITE_EMAILJS_TEMPLATE_ID || ''
const PUBLIC_KEY = import.meta.env.VITE_EMAILJS_PUBLIC_KEY || ''

interface InvitationEmailParams {
  toEmail: string
  inviterName: string
  inviterEmail: string
  role: string
  inviteLink: string
}

export async function sendInvitationEmail(params: InvitationEmailParams): Promise<boolean> {
  if (!isEmailServiceConfigured()) {
    console.warn('EmailJS not fully configured (service id, template id and public key are all required) - email not sent')
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
  return Boolean(SERVICE_ID && TEMPLATE_ID && PUBLIC_KEY)
}
