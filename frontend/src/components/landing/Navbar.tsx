import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, Menu, X, ArrowRight, Code, Palette } from 'lucide-react'
import { THEMES, DEFAULT_THEME } from '../../themes'

const NAV_LINKS = [
  { label: 'Features', href: '#features' },
  { label: 'Pipeline', href: '#pipeline' },
  { label: 'Docs', href: '#docs' },
  { label: 'Blog', href: '#blog' },
  { label: 'Pricing', href: '#pricing' }
]

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export function Navbar({ onLaunch }: { onLaunch: () => void }) {
  const [isScrolled, setIsScrolled] = useState(false)
  const [isMobileOpen, setIsMobileOpen] = useState(false)
  const [isThemeOpen, setIsThemeOpen] = useState(false)
  const [currentTheme, setCurrentTheme] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('copilot-theme') || DEFAULT_THEME
    }
    return DEFAULT_THEME
  })

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 50)
    }

    window.addEventListener('scroll', handleScroll)
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = currentTheme
    localStorage.setItem('copilot-theme', currentTheme)
  }, [currentTheme])

  const handleThemeChange = (themeId: string) => {
    setCurrentTheme(themeId)
    setIsThemeOpen(false)
  }

  return (
    <>
      <motion.nav
        className={`
          fixed top-4 left-4 right-4 z-50 rounded-2xl transition-all duration-500
          ${isScrolled ? 'glass-strong shadow-glass' : 'bg-transparent'}
        `}
        initial={{ y: -100, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.8, ease: [0.4, 0, 0.2, 1] }}
      >
        <div className="flex items-center justify-between px-6 py-4">
          {/* Logo */}
          <a href="/" className="flex items-center gap-2 group" data-testid="nav-brand">
            <motion.div
              className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-accent flex items-center justify-center"
              whileHover={{ rotate: 180, scale: 1.1 }}
              transition={{ duration: 0.5 }}
            >
              <Sparkles className="w-5 h-5 text-background" />
            </motion.div>
            <span className="font-display text-xl font-bold text-white hidden sm:block">
              OmniPotent
            </span>
          </a>

          {/* Desktop links */}
          <div className="hidden md:flex items-center gap-1">
            {NAV_LINKS.map((link) => (
              <a
                key={link.label}
                href={link.href}
                className="px-4 py-2 text-white/70 hover:text-white font-medium text-sm transition-colors relative group"
              >
                {link.label}
                <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-0 h-0.5 bg-primary transition-all group-hover:w-full" />
              </a>
            ))}
            <a
              href={`${API_BASE_URL}/docs`}
              target="_blank"
              rel="noreferrer"
              className="px-4 py-2 text-white/70 hover:text-white font-medium text-sm transition-colors relative group flex items-center gap-1.5"
            >
              <Code className="w-3.5 h-3.5" />
              API
              <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-0 h-0.5 bg-primary transition-all group-hover:w-full" />
            </a>
          </div>

          {/* CTA */}
          <div className="flex items-center gap-3">
            {/* Theme Switcher */}
            <div className="relative">
              <button
                onClick={() => setIsThemeOpen(!isThemeOpen)}
                className="p-2 rounded-lg hover:bg-white/10 transition-colors"
                aria-label="Change theme"
              >
                <Palette className="w-5 h-5 text-white/70 hover:text-white" />
              </button>

              <AnimatePresence>
                {isThemeOpen && (
                  <>
                    <motion.div
                      className="fixed inset-0 z-40"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      onClick={() => setIsThemeOpen(false)}
                    />
                    <motion.div
                      className="absolute right-0 top-12 z-50 w-64 p-3 rounded-xl bg-[#0a0f1a]/95 backdrop-blur-xl border border-white/10 shadow-2xl"
                      initial={{ opacity: 0, y: -10, scale: 0.95 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: -10, scale: 0.95 }}
                      transition={{ duration: 0.15 }}
                    >
                      <p className="text-white/50 text-xs uppercase tracking-wider mb-3 px-2">
                        Choose Theme
                      </p>
                      <div className="grid grid-cols-2 gap-2">
                        {THEMES.map((theme) => (
                          <button
                            key={theme.id}
                            onClick={() => handleThemeChange(theme.id)}
                            className={`flex items-center gap-2 p-2 rounded-lg transition-all text-left ${
                              currentTheme === theme.id
                                ? 'bg-white/10 border border-white/20'
                                : 'hover:bg-white/5 border border-transparent'
                            }`}
                          >
                            <div
                              className="w-5 h-5 rounded-md flex-shrink-0"
                              style={{
                                background: `linear-gradient(135deg, ${theme.swatch[0]}, ${theme.swatch[1]})`,
                              }}
                            />
                            <span className="text-white/80 text-xs font-medium truncate">
                              {theme.name}
                            </span>
                          </button>
                        ))}
                      </div>
                    </motion.div>
                  </>
                )}
              </AnimatePresence>
            </div>

            <button
              onClick={onLaunch}
              className="hidden sm:block text-white/70 hover:text-white text-sm font-medium transition-colors"
            >
              Sign in
            </button>
            <motion.button
              className="
                flex items-center gap-2 px-5 py-2.5
                bg-gradient-to-r from-primary to-accent
                text-background font-semibold text-sm rounded-xl
                shadow-glow-primary transition-all
              "
              onClick={onLaunch}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.98 }}
              // "Launch Console" is not a unique accessible name on this page
              // (the mobile menu, the hero and AIChat all offer the same CTA),
              // so the E2E suite addresses this one by test id.
              data-testid="nav-launch-console"
            >
              Launch Console
              <ArrowRight className="w-4 h-4" />
            </motion.button>

            {/* Mobile menu button */}
            <button
              className="md:hidden p-2 rounded-lg hover:bg-white/10 transition-colors"
              onClick={() => setIsMobileOpen(!isMobileOpen)}
            >
              {isMobileOpen ? (
                <X className="w-6 h-6 text-white" />
              ) : (
                <Menu className="w-6 h-6 text-white" />
              )}
            </button>
          </div>
        </div>
      </motion.nav>

      {/* Mobile menu */}
      <AnimatePresence>
        {isMobileOpen && (
          <motion.div
            className="fixed inset-0 z-40 md:hidden"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {/* Backdrop */}
            <motion.div
              className="absolute inset-0 bg-background/90 backdrop-blur-xl"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setIsMobileOpen(false)}
            />

            {/* Menu content */}
            <motion.div
              className="relative flex flex-col items-center justify-center h-full gap-8"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 20 }}
              transition={{ delay: 0.1 }}
            >
              {NAV_LINKS.map((link, i) => (
                <motion.a
                  key={link.label}
                  href={link.href}
                  className="text-3xl font-display font-bold text-white hover:text-primary transition-colors"
                  onClick={() => setIsMobileOpen(false)}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1 + i * 0.05 }}
                >
                  {link.label}
                </motion.a>
              ))}

              <motion.button
                className="
                  mt-8 flex items-center gap-2 px-8 py-4
                  bg-gradient-to-r from-primary to-accent
                  text-background font-display font-semibold text-lg rounded-xl
                  shadow-glow-primary
                "
                onClick={() => {
                  setIsMobileOpen(false)
                  onLaunch()
                }}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.3 }}
              >
                Launch Console
                <ArrowRight className="w-5 h-5" />
              </motion.button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
