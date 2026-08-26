// Billing API client - connects to backend app/billing/api.py

import type {
  PricingPlan,
  Subscription,
  UsageData,
  UsageStats,
  UsageHistory,
  Invoice,
  PaymentMethod,
  FeatureStatus,
  UpgradeOption,
  ProrationPreview,
  CheckoutResult,
  PortalResult,
  FeatureCheckResult,
  UsageForecast,
  BillingCycle,
  MetricType,
} from '../types/billing';

import { authHeaders } from '../lib/authToken';
import { API_BASE, describeFailure } from '../lib/apiClient';

// Same-origin by default, matching apiClient. Defaulting to localhost:8000 sent
// every Billing call from a production browser to the user's own machine.
const BASE_URL = API_BASE;

// The thrown message goes straight into the page's error banner, so it has to
// read as a sentence. It used to be `${status} ${statusText}: ${raw body}`,
// which on a configuration failure rendered as
// `503 : {"detail":"The billing portal is not available: ..."}` — statusText is
// empty over HTTP/2, and the JSON envelope was left in.
async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { headers: authHeaders() });
  if (!res.ok) {
    throw new Error(await describeFailure(res));
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await describeFailure(res));
  }
  return res.json() as Promise<T>;
}

// Plans
export async function listPlans(includeEnterprise = false): Promise<PricingPlan[]> {
  return get<PricingPlan[]>(`/billing/plans?include_enterprise=${includeEnterprise}`);
}

export async function getPlanComparison(): Promise<
  { feature: string; display_name: string; plans: Record<string, boolean> }[]
> {
  return get('/billing/plans/comparison');
}

// Subscription
export async function getSubscription(): Promise<Subscription | null> {
  try {
    return await get<Subscription>('/billing/subscription');
  } catch {
    return null;
  }
}

export async function changeSubscription(
  planName: string,
  billingCycle?: BillingCycle
): Promise<Subscription> {
  return post<Subscription>('/billing/subscription/change', {
    plan_name: planName,
    billing_cycle: billingCycle,
  });
}

export async function cancelSubscription(
  reason = '',
  immediate = false,
  feedback = ''
): Promise<Subscription> {
  return post<Subscription>('/billing/subscription/cancel', {
    reason,
    immediate,
    feedback,
  });
}

export async function reactivateSubscription(): Promise<Subscription> {
  return post<Subscription>('/billing/subscription/reactivate', {});
}

export async function calculateProration(newPlanName: string): Promise<ProrationPreview> {
  return get<ProrationPreview>(`/billing/subscription/proration?plan=${newPlanName}`);
}

// Usage
export async function getUsage(): Promise<UsageData> {
  return get<UsageData>('/billing/usage');
}

export async function getUsageStats(yearMonth?: string): Promise<UsageStats> {
  const path = yearMonth ? `/billing/usage/stats?year_month=${yearMonth}` : '/billing/usage/stats';
  return get<UsageStats>(path);
}

export async function getUsageHistory(months = 12): Promise<UsageHistory[]> {
  return get<UsageHistory[]>(`/billing/usage/history?months=${months}`);
}

export async function getUsageReport(yearMonth?: string): Promise<Record<string, unknown>> {
  const path = yearMonth ? `/billing/usage/report?year_month=${yearMonth}` : '/billing/usage/report';
  return get<Record<string, unknown>>(path);
}

export async function getUsageForecast(monthsAhead = 3): Promise<UsageForecast> {
  return get<UsageForecast>(`/billing/usage/forecast?months_ahead=${monthsAhead}`);
}

// Checkout & Portal
export async function createCheckout(
  planName: string,
  billingCycle: BillingCycle = 'monthly',
  email?: string,
  successUrl?: string,
  cancelUrl?: string
): Promise<CheckoutResult> {
  return post<CheckoutResult>('/billing/checkout', {
    plan_name: planName,
    billing_cycle: billingCycle,
    email,
    success_url: successUrl,
    cancel_url: cancelUrl,
  });
}

export async function createPortalSession(returnUrl?: string): Promise<PortalResult> {
  return post<PortalResult>('/billing/portal', { return_url: returnUrl });
}

// Invoices & Payment Methods
export async function getInvoices(limit = 10): Promise<Invoice[]> {
  return get<Invoice[]>(`/billing/invoices?limit=${limit}`);
}

export async function getPaymentMethods(): Promise<PaymentMethod[]> {
  return get<PaymentMethod[]>('/billing/payment-methods');
}

// Feature Gating
export async function checkFeature(featureKey: string): Promise<FeatureCheckResult> {
  return get<FeatureCheckResult>(`/billing/features/${featureKey}`);
}

export async function getFeatureStatus(
  features?: string[]
): Promise<Record<string, FeatureStatus>> {
  const path = features
    ? `/billing/features?features=${features.join(',')}`
    : '/billing/features';
  return get<Record<string, FeatureStatus>>(path);
}

export async function getUpgradeOptions(): Promise<UpgradeOption[]> {
  return get<UpgradeOption[]>('/billing/upgrade-options');
}

// Track usage (for internal use)
export async function trackUsage(
  metricType: MetricType,
  metadata?: Record<string, unknown>
): Promise<{ tracked: boolean }> {
  return post<{ tracked: boolean }>('/billing/track', {
    metric_type: metricType,
    metadata,
  });
}

// Check usage limit before action
export async function checkUsageLimit(
  metricType: MetricType,
  increment = 1
): Promise<{
  within_limit: boolean;
  reason: string | null;
  current_usage: number;
  limit: number | null;
  remaining: number | null;
}> {
  return get(`/billing/usage/limit?metric=${metricType}&increment=${increment}`);
}
