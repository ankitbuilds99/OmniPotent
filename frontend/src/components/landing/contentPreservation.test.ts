import { describe, expect, it } from 'vitest'
import heroSource from './HeroSection.tsx?raw'
import landingSource from './CinematicLanding.tsx?raw'
import mediaSource from './landingMedia.ts?raw'
import aiChatSource from './AIChat.tsx?raw'

describe('landing content preservation', () => {
  it('keeps the approved hero copy and rotating words', () => {
    const source = heroSource
    for (const text of [
      'Now Generally Available',
      'AI agents that',
      'resolve',
      'incidents',
      'outages',
      'alerts',
      'failures',
      'pages',
      'Your on-call engineers get back to building.',
      'Start Free Trial',
      'Try Demo - No Login',
      'Integrates with tools you use',
    ]) {
      expect(source).toContain(text)
    }
  })

  it('keeps the original landing section order and destinations', () => {
    const source = landingSource
    const order = [
      'HeroSection',
      'HowItWorks',
      'Terminal',
      'Pipeline',
      'Dashboard',
      'AIChat',
      'SecuritySection',
      'Metrics',
      'Documentation',
      'Blog',
      'Footer',
    ]
    let previous = -1
    for (const component of order) {
      const index = source.indexOf(`<${component}`)
      expect(index).toBeGreaterThan(previous)
      previous = index
    }
    expect(source).not.toContain('<FeatureCards')
    expect(source).toContain('id="pipeline"')
    expect(source).toContain('id="dashboard"')
    expect(source).not.toContain('<Pricing')
    expect(source).not.toContain('GlobalMap')
  })

  it('starts a direct marketing reload at the hero unless a section hash is present', () => {
    expect(landingSource).toContain("window.history.scrollRestoration = 'manual'")
    expect(landingSource).toContain("window.scrollTo({ top: 0, left: 0, behavior: 'auto' })")
    expect(landingSource).toContain('if (!window.location.hash)')
  })

  it('keeps the AI demo autoplay inside its own message panel', () => {
    expect(aiChatSource).toContain('messagesContainerRef.current')
    expect(aiChatSource).toContain('messagesContainer.scrollTo')
    expect(aiChatSource).not.toContain('messagesEndRef.current?.scrollIntoView')
  })

  it('uses the specified media assets without replacing product sections', () => {
    const media = mediaSource
    expect(media).toContain('hf_20260818_072341_50851634-bbc3-4c33-9acc-7647d4db44aa.mp4')
  })
})
