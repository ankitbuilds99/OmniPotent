import { type SimpleIcon, siArgo, siDatadog, siDocker, siGithub, siGooglecloud, siKubernetes, siPagerduty, siPrometheus, siSentry, siTerraform } from "simple-icons";

import { Icon } from "./Icon";

const COLORS: Record<string, string> = {
  omnipotent: "#e2a400", openai: "#6538ed", anthropic: "#191919", gemini: "#3478f6",
  groq: "#111827", deepseek: "#2563eb", datadog: "#632ca6", github: "#181717",
  pagerduty: "#06AC38", kubernetes: "#326CE5", aws: "#e28b18", slack: "#611f69",
  sentry: "#362D59", terraform: "#844FBA", docker: "#2496ED", googlecloud: "#4285F4",
  prometheus: "#E6522C", argo: "#EF7B4D",
};

const VERIFIED_MARKS: Record<string, SimpleIcon> = {
  argo: siArgo,
  datadog: siDatadog,
  docker: siDocker,
  github: siGithub,
  googlecloud: siGooglecloud,
  kubernetes: siKubernetes,
  pagerduty: siPagerduty,
  prometheus: siPrometheus,
  sentry: siSentry,
  terraform: siTerraform,
};

function SimpleIconMark({ icon }: { icon: SimpleIcon }) {
  return <svg viewBox="0 0 24 24" className="integration-logo__svg integration-logo__svg--brand" aria-hidden="true"><path fill="currentColor" d={icon.path} /></svg>;
}

function OpenAiMark() {
  return <svg viewBox="0 0 24 24" className="integration-logo__svg" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
    <path d="M9.1 4.2a4 4 0 0 1 6.7 1.9 4 4 0 0 1 2.8 6 4 4 0 0 1-3.9 6.3 4 4 0 0 1-6.7-1.9 4 4 0 0 1-2.8-6 4 4 0 0 1 3.9-6.3Z" />
    <path d="m8.1 8.5 3.8-2.2 4.1 2.3v4.5l-3.9 2.3-4-2.3Z" />
    <path d="m11.9 6.3 4.1 2.3M15.9 13.1l2.7-1.6M12.1 15.4v3.1M8.1 13.1l-2.7-1.6M8.1 8.5V5.4" />
  </svg>;
}

function SlackMark() {
  return <svg viewBox="0 0 24 24" className="integration-logo__svg" aria-hidden="true"><path fill="#36c5f0" d="M7.4 2.5a2.7 2.7 0 1 0 0 5.4h2.7V5.2a2.7 2.7 0 0 0-2.7-2.7Z" /><path fill="#2eb67d" d="M21.5 7.4a2.7 2.7 0 1 0-5.4 0v2.7h2.7a2.7 2.7 0 0 0 2.7-2.7Z" /><path fill="#ecb22e" d="M16.6 21.5a2.7 2.7 0 1 0 0-5.4h-2.7v2.7a2.7 2.7 0 0 0 2.7 2.7Z" /><path fill="#e01e5a" d="M2.5 16.6a2.7 2.7 0 1 0 5.4 0v-2.7H5.2a2.7 2.7 0 0 0-2.7 2.7Z" /><path fill="#36c5f0" d="M10.1 7.9H7.4a2.7 2.7 0 0 0 0 5.4h2.7V7.9Z" /><path fill="#2eb67d" d="M16.1 10.1V7.4a2.7 2.7 0 1 0-5.4 0v2.7h5.4Z" /><path fill="#ecb22e" d="M13.9 16.1h2.7a2.7 2.7 0 1 0 0-5.4h-2.7v5.4Z" /><path fill="#e01e5a" d="M7.9 13.9v2.7a2.7 2.7 0 1 0 5.4 0v-2.7H7.9Z" /></svg>;
}

function ProviderMark({ id }: { id: string }) {
  if (id === "omnipotent") return <Icon name="sparkles" size={18} />;
  if (VERIFIED_MARKS[id]) return <SimpleIconMark icon={VERIFIED_MARKS[id]} />;
  if (id === "openai" || id === "anthropic") return <OpenAiMark />;
  if (id === "slack") return <SlackMark />;
  if (id === "aws") return <span className="integration-logo__aws">aws<em>⌒</em></span>;
  return <span className="integration-logo__label">{id.slice(0, 1).toUpperCase()}</span>;
}

/** Uses verified Simple Icons path data where available and local fallbacks for brands absent from that catalog. */
export function IntegrationLogo({ name, size = 30 }: { name: string; size?: number }) {
  const id = name.toLowerCase().replace(/[^a-z0-9]/g, "");
  const markId = id === "repo" ? "github" : id;
  return <span className={`integration-logo integration-logo--${markId}`} style={{ color: COLORS[markId] ?? "#52627e", width: size, height: size }} aria-hidden="true"><ProviderMark id={markId} /></span>;
}
