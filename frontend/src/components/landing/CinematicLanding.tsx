import { useEffect, useRef, useState } from 'react'
import Lenis from 'lenis'
import { Navbar } from './Navbar'
import { HeroSection } from './HeroSection'
import { HowItWorks } from './HowItWorks'
import { Terminal } from './Terminal'
import { Pipeline } from './Pipeline'
import { Dashboard } from './Dashboard'
import { AIChat } from './AIChat'
import { SecuritySection } from './SecuritySection'
import { Metrics } from './Metrics'
import { Documentation } from './Documentation'
import { Blog } from './Blog'
import { Footer } from './Footer'
import { CursorGlow } from './CursorGlow'
import { DecorativeVideo } from './DecorativeVideo'
import { LANDING_MEDIA } from './landingMedia'
import { PageBackground } from '../PageBackground'

export function CinematicLanding({ onLaunch, onDashboard, onBilling: _onBilling }: { onLaunch: () => void; onDashboard?: () => void; onBilling?: () => void }) {
  const handleTryDemo = () => {
    localStorage.setItem('demo_user', JSON.stringify({ email: 'demo@omnipotent.com', name: 'Demo User' }))
    if (onDashboard) onDashboard()
  }
  const containerRef = useRef<HTMLDivElement>(null)
  const [ambientPaused, setAmbientPaused] = useState(false)

  // Smooth scrolling with Lenis
  useEffect(() => {
    const previousScrollRestoration = window.history.scrollRestoration
    window.history.scrollRestoration = 'manual'

    // Browsers can restore the previous scroll position on a hard reload. A
    // fresh marketing route should begin at the hero unless an explicit
    // section hash was requested.
    if (!window.location.hash) {
      window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
    } else {
      const targetId = decodeURIComponent(window.location.hash.slice(1))
      requestAnimationFrame(() => {
        document.getElementById(targetId)?.scrollIntoView({
          behavior: 'auto',
          block: 'start'
        })
      })
    }

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return () => {
        window.history.scrollRestoration = previousScrollRestoration
      }
    }
    const lenis = new Lenis({
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      orientation: 'vertical',
      smoothWheel: true
    })

    let rafId: number
    function raf(time: number) {
      lenis.raf(time)
      rafId = requestAnimationFrame(raf)
    }

    rafId = requestAnimationFrame(raf)

    return () => {
      cancelAnimationFrame(rafId)
      lenis.destroy()
      window.history.scrollRestoration = previousScrollRestoration
    }
  }, [])

  return (
    <div
      ref={containerRef}
      className="relative min-h-screen omni-landing"
      style={{ backgroundColor: 'var(--bg)' }}
    >
      <PageBackground />
      <DecorativeVideo
        src={LANDING_MEDIA.hero}
        mode="hero-loop"
        preload="auto"
        paused={ambientPaused}
        className="fixed inset-0 z-[1] h-full w-full object-cover object-center omni-landing__shared-video"
      />
      {/* Cursor glow effect */}
      <CursorGlow />

      {/* Nebula background */}
      <div className="nebula-bg" />

      {/* Navigation */}
      <Navbar onLaunch={onLaunch} />

      {/* Main content */}
      <main>
        {/* Hero with 3D scene + integrations */}
        <HeroSection
          onLaunch={onLaunch}
          onTryDemo={handleTryDemo}
          ambientPaused={ambientPaused}
          onAmbientPauseChange={setAmbientPaused}
        />

        {/* How It Works - Interactive Tutorial */}
        <HowItWorks onLaunch={onLaunch} />

        {/* Terminal demo */}
        <Terminal />

        {/* Pipeline visualization */}
        <section id="pipeline">
          <Pipeline />
        </section>

        {/* Live dashboard */}
        <section id="dashboard">
          <Dashboard />
        </section>

        {/* AI Chat interface */}
        <AIChat onLaunch={onLaunch} />

        {/* Security Section - Enterprise grade */}
        <SecuritySection />

        {/* Metrics */}
        <Metrics />

        {/* Documentation */}
        <Documentation />

        {/* Blog */}
        <Blog />
      </main>

      {/* Footer with 3D scene */}
      <Footer onLaunch={onLaunch} />
    </div>
  )
}
