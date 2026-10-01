import { useEffect } from 'react'
import { Navbar } from '../components/landing/Navbar'
import { Pricing } from '../components/landing/Pricing'
import { DecorativeVideo } from '../components/landing/DecorativeVideo'
import { LANDING_MEDIA } from '../components/landing/landingMedia'
import { PageBackground } from '../components/PageBackground'

export function PricingPage({ onLaunch }: { onLaunch: () => void }) {
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
  }, [])

  return (
    <div className="relative min-h-screen omni-landing">
      <PageBackground />
      <DecorativeVideo
        src={LANDING_MEDIA.hero}
        mode="hero-loop"
        preload="auto"
        className="fixed inset-0 z-[1] h-full w-full object-cover object-center omni-landing__shared-video"
      />
      <Navbar onLaunch={onLaunch} />
      <main className="relative z-[2] pt-24">
        <Pricing onLaunch={onLaunch} />
      </main>
    </div>
  )
}
