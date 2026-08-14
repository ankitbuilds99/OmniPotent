import { useEffect, useState } from "react";

import { getFrontendRuntimeConfig } from "./runtime";

const DEFAULT_PROJECT_REPO_URL = "https://github.com/ankit25bcs10610/OmniPotent";

function normalizeRepoUrl(url: string): string {
  return url.replace(/\/$/, "");
}

export async function resolveProjectRepoUrl(): Promise<string> {
  try {
    const runtimeConfig = await getFrontendRuntimeConfig();
    const runtimeRepo = runtimeConfig.project_repo_url?.trim();
    if (runtimeRepo) return normalizeRepoUrl(runtimeRepo);
  } catch {
    // Fall back to local env/default below.
  }

  const envRepo = (import.meta.env.VITE_PROJECT_REPO_URL as string | undefined)?.trim();
  return normalizeRepoUrl(envRepo || DEFAULT_PROJECT_REPO_URL);
}

export function useProjectRepoUrl(): string {
  const [repoUrl, setRepoUrl] = useState(normalizeRepoUrl(DEFAULT_PROJECT_REPO_URL));

  useEffect(() => {
    let active = true;
    void resolveProjectRepoUrl()
      .then((url) => {
        if (active) setRepoUrl(url);
      })
      .catch(() => {
        if (active) setRepoUrl(normalizeRepoUrl(DEFAULT_PROJECT_REPO_URL));
      });
    return () => {
      active = false;
    };
  }, []);

  return repoUrl;
}

export function buildProjectRepoLinks(repoUrl: string) {
  return {
    repo: repoUrl,
    commits: `${repoUrl}/commits/main`,
    issues: `${repoUrl}/issues`,
    readme: `${repoUrl}#readme`,
    features: `${repoUrl}#features`,
    hitl: `${repoUrl}#human-in-the-loop-approval`,
    mcp: `${repoUrl}/tree/main/app/mcp`,
  };
}
