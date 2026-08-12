const DEFAULT_PROJECT_REPO_URL = "https://github.com/ankit25bcs10610/OmniPotent";

const baseRepoUrl = (import.meta.env.VITE_PROJECT_REPO_URL as string | undefined)?.trim() || DEFAULT_PROJECT_REPO_URL;
export const PROJECT_REPO_URL = baseRepoUrl.replace(/\/$/, "");

export const PROJECT_REPO_COMMITS_URL = `${PROJECT_REPO_URL}/commits/main`;
export const PROJECT_REPO_ISSUES_URL = `${PROJECT_REPO_URL}/issues`;
export const PROJECT_REPO_README_URL = `${PROJECT_REPO_URL}#readme`;
export const PROJECT_REPO_FEATURES_URL = `${PROJECT_REPO_URL}#features`;
export const PROJECT_REPO_HITL_URL = `${PROJECT_REPO_URL}#human-in-the-loop-approval`;
export const PROJECT_REPO_MCP_URL = `${PROJECT_REPO_URL}/tree/main/app/mcp`;

