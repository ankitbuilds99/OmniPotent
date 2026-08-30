import { motion } from 'framer-motion'
import { Shield, Lock, Eye, Server, Key, FileCheck, CheckCircle2 } from 'lucide-react'

/** De-faked: this list used to claim 'SOC 2 Type II Compliant' and
 *  'GDPR & CCPA Ready' — certifications a private-beta project has never
 *  obtained or been assessed for. What remains are architectural facts
 *  about how the product is actually built and deployed. */
const SECURITY_FEATURES = [
  { icon: Shield, text: 'Self-hosted deployment' },
  { icon: Lock, text: 'Human approval before changes' },
  { icon: Eye, text: 'GDPR-friendly data controls' },
  { icon: Server, text: 'Your data stays in your cloud' },
  { icon: Key, text: 'RBAC & SSO support' },
  { icon: FileCheck, text: 'Audit logging' },
]


export function SecuritySection() {
  return (
    <section className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-[#030508] via-[#050a14] to-[#030508]" />

      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-[#7C3AED]/5 rounded-full blur-[120px]" />

      <div className="max-w-7xl mx-auto px-6 relative z-10">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          <motion.div
            initial={{ opacity: 0, x: -30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6 }}
          >
            <span className="inline-block text-[#00E7FF] text-sm font-semibold tracking-wider uppercase mb-4">
              Enterprise Security
            </span>
            <h2 className="text-4xl md:text-5xl font-bold text-white mb-6 leading-tight">
              Built for{' '}
              <span className="bg-gradient-to-r from-[#00E7FF] to-[#7C3AED] bg-clip-text text-transparent">
                enterprise
              </span>{' '}
              security requirements
            </h2>
            <p className="text-white/60 text-lg mb-10 leading-relaxed">
              Your infrastructure data never leaves your environment. The architecture is
              designed so your data stays under your control at every step.
            </p>

            <div className="grid sm:grid-cols-2 gap-4">
              {SECURITY_FEATURES.map((feature, i) => (
                <motion.div
                  key={feature.text}
                  className="flex items-center gap-3 p-4 rounded-xl bg-white/[0.03] border border-white/[0.06] hover:bg-white/[0.05] hover:border-[#00E7FF]/30 transition-all duration-300"
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.4, delay: i * 0.1 }}
                >
                  <div className="w-10 h-10 rounded-lg bg-[#00E7FF]/10 flex items-center justify-center flex-shrink-0">
                    <feature.icon className="w-5 h-5 text-[#00E7FF]" />
                  </div>
                  <span className="text-white/80 text-sm font-medium">{feature.text}</span>
                </motion.div>
              ))}
            </div>
          </motion.div>

          <motion.div
            className="relative"
            initial={{ opacity: 0, x: 30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, delay: 0.2 }}
          >
            <div className="relative p-8 rounded-3xl bg-gradient-to-br from-white/[0.05] to-white/[0.02] border border-white/[0.08] backdrop-blur-xl">
              <div className="absolute -top-px left-8 right-8 h-px bg-gradient-to-r from-transparent via-[#00E7FF]/50 to-transparent" />

              <div className="text-center mb-8">
                <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-[#00E7FF]/20 to-[#7C3AED]/20 border border-white/10 mb-4">
                  <Shield className="w-10 h-10 text-[#00E7FF]" />
                </div>
                <h3 className="text-2xl font-bold text-white mb-2">Security First</h3>
                <p className="text-white/50 text-sm">Your data stays in your cloud</p>
              </div>

              {/* De-faked: 'Regular third-party security audits' claimed audits
                  that have never been commissioned; the remaining items describe
                  the deployment model as it exists. */}
              <div className="space-y-4">
                {[
                  'No customer data stored on our servers',
                  'Self-hosted deployment options',
                  'VPC and private network support',
                  'Configurable data retention policies',
                ].map((item, i) => (
                  <motion.div
                    key={item}
                    className="flex items-center gap-3"
                    initial={{ opacity: 0, x: 20 }}
                    whileInView={{ opacity: 1, x: 0 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.3, delay: 0.4 + i * 0.1 }}
                  >
                    <CheckCircle2 className="w-5 h-5 text-[#00FFB3] flex-shrink-0" />
                    <span className="text-white/70 text-sm">{item}</span>
                  </motion.div>
                ))}
              </div>

              {/* A "Compliance Certifications" badge row used to sit here,
                  showing SOC 2 / ISO 27001 / GDPR / HIPAA. None of those
                  certifications exist for this project, and rendering the
                  logos of audits that never happened is a legal claim, not
                  decoration. Removed outright in the de-faking pass — it
                  comes back if and when a real certificate does. */}
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  )
}
