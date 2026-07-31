import { useEffect, useState } from 'react'

export interface ThemeColors {
  accent: string
  accent2: string
  ok: string
  bg: string
  shell: string
  panel: string
  border: string
  text: string
  textDim: string
  warn: string
  bad: string
}

const DEFAULT_COLORS: ThemeColors = {
  accent: '#6491ff',
  accent2: '#7c5cff',
  ok: '#2dd4a7',
  bg: '#070a10',
  shell: '#0e131c',
  panel: '#141b27',
  border: '#232e40',
  text: '#eaf0f8',
  textDim: '#94a3b8',
  warn: '#f5b544',
  bad: '#ff6b6b'
}

export function useThemeColors(): ThemeColors {
  const [colors, setColors] = useState<ThemeColors>(DEFAULT_COLORS)

  useEffect(() => {
    const updateColors = () => {
      const style = getComputedStyle(document.documentElement)
      setColors({
        accent: style.getPropertyValue('--accent').trim() || DEFAULT_COLORS.accent,
        accent2: style.getPropertyValue('--accent-2').trim() || DEFAULT_COLORS.accent2,
        ok: style.getPropertyValue('--ok').trim() || DEFAULT_COLORS.ok,
        bg: style.getPropertyValue('--bg').trim() || DEFAULT_COLORS.bg,
        shell: style.getPropertyValue('--shell').trim() || DEFAULT_COLORS.shell,
        panel: style.getPropertyValue('--panel').trim() || DEFAULT_COLORS.panel,
        border: style.getPropertyValue('--border').trim() || DEFAULT_COLORS.border,
        text: style.getPropertyValue('--text').trim() || DEFAULT_COLORS.text,
        textDim: style.getPropertyValue('--text-dim').trim() || DEFAULT_COLORS.textDim,
        warn: style.getPropertyValue('--warn').trim() || DEFAULT_COLORS.warn,
        bad: style.getPropertyValue('--bad').trim() || DEFAULT_COLORS.bad
      })
    }

    updateColors()

    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'data-theme') {
          setTimeout(updateColors, 50)
        }
      })
    })

    observer.observe(document.documentElement, { attributes: true })
    return () => observer.disconnect()
  }, [])

  return colors
}

export function getThemeColors(): ThemeColors {
  if (typeof window === 'undefined') return DEFAULT_COLORS

  const style = getComputedStyle(document.documentElement)
  return {
    accent: style.getPropertyValue('--accent').trim() || DEFAULT_COLORS.accent,
    accent2: style.getPropertyValue('--accent-2').trim() || DEFAULT_COLORS.accent2,
    ok: style.getPropertyValue('--ok').trim() || DEFAULT_COLORS.ok,
    bg: style.getPropertyValue('--bg').trim() || DEFAULT_COLORS.bg,
    shell: style.getPropertyValue('--shell').trim() || DEFAULT_COLORS.shell,
    panel: style.getPropertyValue('--panel').trim() || DEFAULT_COLORS.panel,
    border: style.getPropertyValue('--border').trim() || DEFAULT_COLORS.border,
    text: style.getPropertyValue('--text').trim() || DEFAULT_COLORS.text,
    textDim: style.getPropertyValue('--text-dim').trim() || DEFAULT_COLORS.textDim,
    warn: style.getPropertyValue('--warn').trim() || DEFAULT_COLORS.warn,
    bad: style.getPropertyValue('--bad').trim() || DEFAULT_COLORS.bad
  }
}
