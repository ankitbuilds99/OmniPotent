import { useEffect, useRef } from 'react'
import Lenis from 'lenis'
import { Navbar } from './Navbar'
import { HeroSection } from './HeroSection'
import { HowItWorks } from './HowItWorks'
import { Terminal } from './Terminal'
import { FeatureCards } from './FeatureCards'
import { Pipeline } from './Pipeline'
import { GlobalMap } from './GlobalMap'
import { Dashboard } from './Dashboard'
import { AIChat } from './AIChat'
import { TechStack } from './TechStack'
import { Metrics } from './Metrics'
import { Testimonials } from './Testimonials'
import { Pricing } from './Pricing'
import { Footer } from './Footer'
import { CursorGlow } from './CursorGlow'

export function CinematicLanding({ onLaunch }: { onLaunch: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Smooth scrolling with Lenis
  useEffect(() => {
    const lenis = new Lenis({
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      orientation: 'vertical',
      smoothWheel: true
    })

    function raf(time: number) {
      lenis.raf(time)
      requestAnimationFrame(raf)
    }

    requestAnimationFrame(raf)

    return () => {
      lenis.destroy()
    }
  }, [])

  return (
    <div
      ref={containerRef}
      className="relative min-h-screen bg-background overflow-hidden"
    >
      {/* Cursor glow effect */}
      <CursorGlow />

      {/* Nebula background */}
      <div className="nebula-bg" />

      {/* Navigation */}
      <Navbar onLaunch={onLaunch} />

      {/* Main content */}
      <main>
        {/* Hero with 3D scene */}
        <HeroSection onLaunch={onLaunch} />

        {/* How It Works - Interactive Tutorial */}
        <HowItWorks />

        {/* Terminal demo */}
        <Terminal />

        {/* Features section */}
        <section id="features">
          <FeatureCards />
        </section>

        {/* Pipeline visualization */}
        <section id="pipeline">
          <Pipeline />
        </section>

        {/* Global cloud map */}
        <GlobalMap />

        {/* Live dashboard */}
        <section id="dashboard">
          <Dashboard />
        </section>

        {/* AI Chat interface */}
        <AIChat />

        {/* Tech stack */}
        <TechStack />

        {/* Metrics */}
        <Metrics />

        {/* Testimonials */}
        <Testimonials />

        {/* Pricing */}
        <section id="pricing">
          <Pricing />
        </section>
      </main>

      {/* Footer with 3D scene */}
      <Footer onLaunch={onLaunch} />
    </div>
  )
}
