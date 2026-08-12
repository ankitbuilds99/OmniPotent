type RuntimeFrontendConfig = {
  api_url?: string;
  supabase_url?: string;
  supabase_anon_key?: string;
};

const FALLBACK_BASE_URL =
  (import.meta.env.VITE_API_URL as string | undefined)?.trim() ||
  (typeof window !== "undefined" ? window.location.origin : "");
let runtimeConfigPromise: Promise<RuntimeFrontendConfig> | null = null;

async function fetchRuntimeConfig(): Promise<RuntimeFrontendConfig> {
  if (runtimeConfigPromise) return runtimeConfigPromise;

  runtimeConfigPromise = (async () => {
    try {
      const res = await fetch(`${FALLBACK_BASE_URL.replace(/\/$/, "")}/frontend-config`);
      if (!res.ok) return {};
      return (await res.json()) as RuntimeFrontendConfig;
    } catch {
      return {};
    }
  })();

  return runtimeConfigPromise;
}

export async function resolveApiBaseUrl(): Promise<string> {
  const envUrl = import.meta.env.VITE_API_URL as string | undefined;
  if (envUrl && envUrl.trim()) return envUrl.replace(/\/$/, "");

  const runtimeConfig = await fetchRuntimeConfig();
  if (runtimeConfig.api_url && runtimeConfig.api_url.trim()) {
    return runtimeConfig.api_url.replace(/\/$/, "");
  }

  return FALLBACK_BASE_URL.replace(/\/$/, "");
}

export async function getFrontendRuntimeConfig(): Promise<RuntimeFrontendConfig> {
  return fetchRuntimeConfig();
}
