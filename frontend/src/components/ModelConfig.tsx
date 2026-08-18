import { useEffect, useMemo, useState } from "react";

import { configureModel, getModelCatalog, resetConfig } from "../api";
import type { CatalogModel, ModelCatalog } from "../api";
import { refreshConfig, useConfig } from "../hooks/useConfig";

const PROVIDERS = [
  { id: "anthropic", label: "Anthropic — Claude" },
  { id: "openai", label: "OpenAI — GPT" },
  { id: "gemini", label: "Google — Gemini" },
  { id: "groq", label: "Groq — Llama / open models" },
  { id: "deepseek", label: "DeepSeek" },
];

/** Last-resort list, used only when `GET /api/models` is unreachable.
 *
 *  This replaces a hardcoded ~40-ID array that nothing validated and that offered
 *  several models which never shipped — picking one produced a provider 404 at the
 *  first request. Kept deliberately short: only IDs the backend catalog currently
 *  marks "current", so an offline picker still makes a choice that works. */
const FALLBACK_MODEL_IDS: Record<string, string[]> = {
  anthropic: ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
  openai: ["gpt-4o", "gpt-4o-mini"],
  gemini: ["gemini-2.0-flash"],
  groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
  deepseek: ["deepseek-chat"],
};

interface ModelOption {
  id: string;
  label: string;
}

/** Price hint for one catalog entry.
 *
 *  A null price means the catalog has no *verified* number for that model. Say so
 *  outright: a placeholder figure here would be read as a real quote and budgeted
 *  against. */
function priceLabel(m: CatalogModel): string {
  if (!m.has_pricing || m.input_per_1m === null || m.output_per_1m === null) {
    return "pricing unavailable";
  }
  return `$${m.input_per_1m}/$${m.output_per_1m} per 1M in/out`;
}

function toOption(m: CatalogModel): ModelOption {
  const legacy = m.status === "legacy" ? " · legacy" : "";
  return { id: m.id, label: `${m.display_name || m.id}${legacy} — ${priceLabel(m)}` };
}

/** "Change model" control under the MODEL card — switch provider, pick the exact
 *  main/fast model, and paste an API key without a restart. */
export function ModelConfig() {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [fast, setFast] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  // The server's own resolved provider/model — what an empty field really means.
  const { config } = useConfig();

  // Load the server's catalog the first time the panel opens — the backend is the
  // only authority on which model IDs it will accept and what they cost.
  useEffect(() => {
    if (!open || catalog || catalogError) return;
    let cancelled = false;
    getModelCatalog()
      .then((c) => {
        if (!cancelled) setCatalog(c);
      })
      .catch((e: unknown) => {
        // Not fatal: fall back to the short built-in list and say so below.
        if (!cancelled) setCatalogError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open, catalog, catalogError]);

  const models = useMemo<ModelOption[]>(() => {
    const fromCatalog = catalog?.models.filter(
      (m) => m.provider === provider && m.status !== "retired"
    );
    if (fromCatalog?.length) return fromCatalog.map(toOption);
    return (FALLBACK_MODEL_IDS[provider] ?? []).map((id) => ({ id, label: id }));
  }, [catalog, provider]);

  // What leaving a field empty will ACTUALLY select.
  //
  // Not simply catalog.defaults[provider]: that is only the last link in the
  // chain. The backend resolves `override || catalog default`, where the
  // override is COPILOT_MODEL — a single, provider-agnostic setting that wins
  // for every provider. So on a server with COPILOT_MODEL set, labelling the
  // empty option with the catalog default named a model that would not be used.
  //
  // config.model/fast_model are the server's own resolved answer for the
  // provider it is currently on, so they are authoritative there. If they differ
  // from that provider's catalog default, an override is in force and will carry
  // over to whichever provider is selected here.
  const emptyResolvesTo = useMemo(() => {
    const catalogFor = (p: string) => catalog?.defaults?.[p];
    const current = config?.provider;
    if (config && current && catalogFor(current)) {
      const overridden =
        config.model !== catalogFor(current)!.main ||
        config.fast_model !== catalogFor(current)!.fast;
      if (overridden) return { main: config.model, fast: config.fast_model };
      if (provider === current) return { main: config.model, fast: config.fast_model };
    }
    return catalogFor(provider);
  }, [catalog, config, provider]);

  // Switching provider resets the model picks (each provider has its own list).
  const changeProvider = (p: string) => {
    setProvider(p);
    setModel("");
    setFast("");
  };

  const apply = async () => {
    setError(null);
    setBusy(true);
    try {
      await configureModel(provider, apiKey.trim(), model.trim(), fast.trim());
      await refreshConfig(); // updates the sidebar MODEL card to the chosen models
      setOpen(false);
      setApiKey("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError(null);
    try {
      await resetConfig();
      await refreshConfig();
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="link-btn" onClick={() => setOpen(true)}>
        Change model
      </button>
    );
  }

  return (
    <div className="cfg">
      <label className="cfg__field">
        <span>Provider</span>
        <select value={provider} onChange={(e) => changeProvider(e.target.value)}>
          {PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      <input
        className="gh__input"
        type="password"
        placeholder={`${provider} API key`}
        aria-label="API key"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
      />

      <label className="cfg__field">
        <span>Main model</span>
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">
            {emptyResolvesTo ? `Server default (${emptyResolvesTo.main})` : "Server default"}
          </option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </label>

      <label className="cfg__field">
        <span>Fast model (plan / reflect)</span>
        <select value={fast} onChange={(e) => setFast(e.target.value)}>
          <option value="">
            {emptyResolvesTo ? `Server default (${emptyResolvesTo.fast})` : "Server default"}
          </option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </label>

      {catalogError && (
        <p className="gh__note">
          Model list unavailable ({catalogError}) — showing a short built-in list; other IDs may
          still be valid.
        </p>
      )}
      {error && (
        <div className="gh__error" role="alert">
          {error}
        </div>
      )}
      <div className="cfg__actions">
        <button className="link-btn" onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </button>
        <button className="link-btn cfg__reset" onClick={reset} disabled={busy}>
          Reset
        </button>
        <button className="btn btn--approve cfg__apply" onClick={apply} disabled={busy}>
          {busy ? "Applying…" : "Apply"}
        </button>
      </div>
      <p className="gh__note">
        Key is validated on first use · stored in memory only.
        {catalog ? ` Prices last verified ${catalog.pricing_last_verified} (USD per 1M tokens).` : ""}
      </p>
    </div>
  );
}
