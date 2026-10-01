import { useEffect, useRef, useState } from 'react'

type DecorativeVideoProps = {
  src: string
  className?: string
  mode?: 'hero-loop' | 'ambient'
  preload?: 'none' | 'metadata' | 'auto'
  paused?: boolean
  onPlaybackError?: () => void
}

const FADE_MS = 500

export function DecorativeVideo({
  src,
  className = '',
  mode = 'ambient',
  preload = 'none',
  paused = false,
  onPlaybackError,
}: DecorativeVideoProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const frameRef = useRef<number | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const nearEndRef = useRef(false)
  const [reducedMotion, setReducedMotion] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const updateMotion = () => {
      const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection
      setReducedMotion(motionQuery.matches || Boolean(connection?.saveData))
    }
    updateMotion()
    motionQuery.addEventListener?.('change', updateMotion)
    return () => motionQuery.removeEventListener?.('change', updateMotion)
  }, [])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    let visible = mode === 'hero-loop'
    let disposed = false

    const stopFrame = () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }

    const setOpacity = (value: number) => {
      video.style.opacity = String(value)
    }

    const fadeTo = (value: number, duration = FADE_MS) => {
      const start = performance.now()
      const initial = Number.parseFloat(video.style.opacity || '0')
      stopFrame()
      const animate = (now: number) => {
        if (disposed) return
        const progress = Math.min(1, (now - start) / duration)
        setOpacity(initial + (value - initial) * progress)
        if (progress < 1) frameRef.current = requestAnimationFrame(animate)
      }
      frameRef.current = requestAnimationFrame(animate)
    }

    const shouldPlay = () => !disposed && visible && !paused && !reducedMotion && !document.hidden && !failed
    const start = () => {
      if (!shouldPlay()) {
        video.pause()
        setOpacity(0)
        return
      }
      void video.play().then(() => fadeTo(1)).catch(() => undefined)
    }

    const resetAndReplay = () => {
      if (disposed || mode !== 'hero-loop') return
      setOpacity(0)
      video.currentTime = 0
      timerRef.current = setTimeout(start, 100)
    }

    const checkNearEnd = () => {
      if (mode !== 'hero-loop' || !Number.isFinite(video.duration) || !video.duration) return
      const remaining = video.duration - video.currentTime
      if (remaining <= 0.55 && !nearEndRef.current) {
        nearEndRef.current = true
        fadeTo(0)
      } else if (remaining > 1) {
        nearEndRef.current = false
      }
    }

    const onCanPlay = () => start()
    const onEnded = () => {
      nearEndRef.current = false
      resetAndReplay()
    }
    const onError = () => {
      setFailed(true)
      video.pause()
      setOpacity(0)
      onPlaybackError?.()
    }
    const onVisibility = () => {
      if (document.hidden) video.pause()
      else start()
    }
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      if (visible) start()
      else video.pause()
    }, { threshold: 0.05 })

    observer.observe(video)
    video.addEventListener('canplay', onCanPlay)
    video.addEventListener('timeupdate', checkNearEnd)
    video.addEventListener('ended', onEnded)
    video.addEventListener('error', onError)
    document.addEventListener('visibilitychange', onVisibility)
    start()

    return () => {
      disposed = true
      observer.disconnect()
      stopFrame()
      if (timerRef.current) clearTimeout(timerRef.current)
      video.removeEventListener('canplay', onCanPlay)
      video.removeEventListener('timeupdate', checkNearEnd)
      video.removeEventListener('ended', onEnded)
      video.removeEventListener('error', onError)
      document.removeEventListener('visibilitychange', onVisibility)
      video.pause()
    }
  }, [failed, mode, onPlaybackError, paused, reducedMotion, src])

  return (
    <video
      ref={videoRef}
      className={`omni-landing__decorative-video ${className}`}
      src={src}
      muted
      playsInline
      autoPlay={!reducedMotion && !paused}
      preload={preload}
      aria-hidden="true"
      data-media-state={failed ? 'fallback' : reducedMotion ? 'reduced-motion' : 'ready'}
    />
  )
}
