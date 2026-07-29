// Billing and subscription types - mirrors backend app/billing/models.py

export type PlanTier =
  | 'starter'
  | 'team'
  | 'growth'
  | 'business'
  | 'scale'
  | 'enterprise';

export type BillingCycle = 'monthly' | 'annual';

export type SubscriptionStatus =
  | 'active'
  | 'trialing'
  | 'past_due'
  | 'canceled'
  | 'incomplete'
  | 'incomplete_expired'
  | 'unpaid'
  | 'paused';

export type InvoiceStatus = 'draft' | 'open' | 'paid' | 'void' | 'uncollectible';

export type MetricType =
  | 'ai_investigation'
  | 'alert_processed'
  | 'api_call'
  | 'postmortem_generated'
  | 'auto_fix_pr'
  | 'similar_search';

export interface PlanFeatures {
  ai_investigation: boolean;
  slack_integration: boolean;
  basic_deduplication: boolean;
  github_integration: boolean;
  multi_llm: boolean;
  auto_fix_pr: boolean;
  ai_postmortem: boolean;
  similar_incident_search: boolean;
  advanced_deduplication: boolean;
  mtta_mttr_dashboard: boolean;
  api_access: boolean;
  cascade_detection?: boolean;
  service_dependency_map?: boolean;
  pattern_detection?: boolean;
  scheduled_reports?: boolean;
  sso_saml?: boolean;
  rbac?: boolean;
  audit_logs?: boolean;
  soc2_compliance?: boolean;
  custom_dashboards?: boolean;
  servicenow_integration?: boolean;
  priority_support?: boolean;
  self_hosted?: boolean;
  dedicated_support_manager?: boolean;
  quarterly_business_reviews?: boolean;
  dedicated_infrastructure?: boolean;
  hipaa_compliance?: boolean;
  air_gapped_deployment?: boolean;
  custom_data_residency?: boolean;
  bring_your_own_llm?: boolean;
  custom_sla?: boolean;
  phone_support_24_7?: boolean;
  onsite_training?: boolean;
  limit_history_days: number | null;
  limit_llm_providers: number | null;
  limit_integrations: number | null;
  limit_support_response_hours?: number | null;
}

export interface PricingPlan {
  name: string;
  display_name: string;
  description: string;
  tier: PlanTier;
  monthly_price: number;
  annual_price: number;
  annual_monthly_price: number;
  annual_savings_percent: number;
  max_users: number;
  max_ai_investigations_monthly: number | null;
  max_alerts_monthly: number | null;
  features: PlanFeatures;
  is_current?: boolean;
  is_popular?: boolean;
}

export interface Subscription {
  plan: PricingPlan;
  status: SubscriptionStatus;
  billing_cycle: BillingCycle;
  is_trialing: boolean;
  days_remaining_trial: number | null;
  current_period_end: string | null;
  days_until_renewal: number;
  cancel_at_period_end: boolean;
}

export interface UsageLimits {
  users: {
    limit: number | null;
    used: number;
    unlimited: boolean;
  };
  ai_investigations: {
    limit: number | null;
    used: number;
    unlimited: boolean;
  };
  alerts: {
    limit: number | null;
    used: number;
    unlimited: boolean;
  };
  history_days: number | null;
  llm_providers: number | null;
  integrations: number | null;
}

export interface UsageData {
  plan: {
    name: string;
    display_name: string;
    tier: PlanTier;
  };
  limits: UsageLimits;
  features: PlanFeatures;
}

export interface UsageStats {
  year_month: string;
  ai_investigations: number;
  alerts_processed: number;
  api_calls: number;
  postmortems_generated: number;
  auto_fix_prs: number;
  similar_searches: number;
  active_users: number;
  total_cost_cents: number;
}

export interface UsageHistory {
  year_month: string;
  ai_investigations_count: number;
  alerts_processed_count: number;
  api_calls_count: number;
  postmortems_generated_count: number;
  auto_fix_prs_count: number;
  similar_searches_count: number;
  active_users_count: number;
}

export interface Invoice {
  id: string;
  number: string;
  status: InvoiceStatus;
  amount_due: number;
  amount_paid: number;
  currency: string;
  created_at: string;
  due_date: string | null;
  paid_at: string | null;
  hosted_invoice_url: string | null;
  invoice_pdf: string | null;
}

export interface PaymentMethod {
  id: string;
  type: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  is_default: boolean;
}

export interface FeatureStatus {
  enabled: boolean;
  source: 'plan' | 'override';
  required_plan?: string | null;
  expires_at?: string | null;
}

export interface UpgradeOption {
  name: string;
  display_name: string;
  description: string;
  tier: PlanTier;
  monthly_price: number;
  annual_price: number;
  features: PlanFeatures;
}

export interface ProrationPreview {
  from_plan: string;
  to_plan: string;
  amount_due: number;
  credit: number;
  proration_date: string;
  is_upgrade: boolean;
}

export interface CheckoutResult {
  checkout_url: string;
  session_id: string;
}

export interface PortalResult {
  portal_url: string;
}

export interface FeatureCheckResult {
  available: boolean;
  feature: string;
  reason?: string;
  upgrade?: {
    feature: string;
    current_plan: string;
    required_plan: string;
    required_plan_display: string;
    upgrade_url: string;
    price?: {
      monthly: number | null;
      annual_monthly: number | null;
    };
  };
}

export interface UsageForecast {
  forecasts: {
    year_month: string;
    projected: {
      ai_investigations: number;
      alerts: number;
      api_calls: number;
    };
    limits: {
      ai_investigations: number | null;
      alerts: number | null;
    };
    warnings: string[];
  }[];
  trend: 'increasing' | 'stable' | 'decreasing';
  recommendations: string[];
}
