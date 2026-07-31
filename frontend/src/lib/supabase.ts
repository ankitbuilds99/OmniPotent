/**
 * Supabase client for DevOps-Copilot frontend.
 *
 * This module provides a type-safe wrapper around the Supabase client.
 * It supports both build-time env vars (VITE_SUPABASE_*) and runtime
 * config fetched from the backend API (for Docker deployments where
 * env vars aren't available at build time).
 */

// Build-time env vars (available in dev, may be empty in Docker builds)
let supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string | undefined
let supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined

// Supabase client type - dynamic import means we can't statically type it
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- dynamic Supabase client
type AnyClient = any

let supabaseInstance: AnyClient | null = null
let initPromise: Promise<AnyClient | null> | null = null
let configPromise: Promise<void> | null = null

async function fetchRuntimeConfig(): Promise<void> {
  // If we already have config from build-time, skip runtime fetch
  if (supabaseUrl && supabaseAnonKey) {
    return
  }

  // Only fetch once, but wait for the same promise if called multiple times
  if (configPromise) return configPromise

  configPromise = (async () => {
    try {
      const res = await fetch('/frontend-config')
      if (res.ok) {
        const config = await res.json()
        if (config.supabase_url) supabaseUrl = config.supabase_url
        if (config.supabase_anon_key) supabaseAnonKey = config.supabase_anon_key
        console.log('Supabase config loaded:', { url: supabaseUrl, hasKey: !!supabaseAnonKey })
      }
    } catch (e) {
      console.warn('Failed to fetch frontend config:', e)
    }
  })()

  return configPromise
}

async function createClient(): Promise<AnyClient | null> {
  // First try to get runtime config from API
  await fetchRuntimeConfig()

  if (!supabaseUrl || !supabaseAnonKey) {
    return null
  }

  try {
    const mod = await import('@supabase/supabase-js')
    const create = mod.createClient
    return create(supabaseUrl, supabaseAnonKey, {
      auth: {
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: true,
      },
    })
  } catch {
    console.warn('Supabase SDK not installed. Auth features disabled.')
    return null
  }
}

export async function initSupabase(): Promise<AnyClient | null> {
  if (supabaseInstance) return supabaseInstance
  if (initPromise) return initPromise

  initPromise = createClient().then(client => {
    supabaseInstance = client
    return client
  })

  return initPromise
}

export function getSupabase(): AnyClient | null {
  return supabaseInstance
}

export const supabase = supabaseInstance

export function isSupabaseConfigured(): boolean {
  // Check both build-time and runtime config
  return Boolean(supabaseUrl && supabaseAnonKey)
}

export async function checkSupabaseConfigured(): Promise<boolean> {
  await fetchRuntimeConfig()
  return Boolean(supabaseUrl && supabaseAnonKey)
}

// Auto-init on load
if (typeof window !== 'undefined') {
  initSupabase()
}
