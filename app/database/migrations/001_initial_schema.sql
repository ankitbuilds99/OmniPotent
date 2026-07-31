-- DevOps-Copilot Initial Schema for Supabase
-- Run this in the Supabase SQL Editor to create all tables

-- Enable UUID extension (usually already enabled in Supabase)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Organizations (multi-tenant)
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(256) NOT NULL,
    plan VARCHAR(32) NOT NULL DEFAULT 'free',
    settings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_organizations_slug ON organizations(slug);

-- API Keys for authentication
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key_hash VARCHAR(128) UNIQUE NOT NULL,
    key_prefix VARCHAR(16) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    role VARCHAR(32) NOT NULL DEFAULT 'responder',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_org_active ON api_keys(org_id, is_active);

-- Members (users in an organization)
CREATE TABLE IF NOT EXISTS members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email VARCHAR(256) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'viewer',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(org_id, email)
);

CREATE INDEX IF NOT EXISTS idx_members_org_email ON members(org_id, email);

-- Investigations (core data)
CREATE TABLE IF NOT EXISTS investigations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    thread_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    query TEXT NOT NULL,
    summary TEXT,
    report JSONB,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    feedback_rating VARCHAR(16),
    feedback_comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE(org_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_investigations_thread ON investigations(thread_id);
CREATE INDEX IF NOT EXISTS idx_investigations_org_created ON investigations(org_id, created_at);
CREATE INDEX IF NOT EXISTS idx_investigations_org_thread ON investigations(org_id, thread_id);

-- Investigation Events (trace, approval, tool calls)
CREATE TABLE IF NOT EXISTS investigation_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    investigation_id UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    event_type VARCHAR(32) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_investigation_created ON investigation_events(investigation_id, created_at);

-- Audit Logs (immutable with hash chain)
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    org_id UUID,
    actor VARCHAR(256),
    event_type VARCHAR(64) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    prev_hash VARCHAR(64) NOT NULL,
    entry_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_entry_hash ON audit_logs(entry_hash);
CREATE INDEX IF NOT EXISTS idx_audit_org_created ON audit_logs(org_id, created_at);

-- Alert Rules
CREATE TABLE IF NOT EXISTS alert_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID,
    name VARCHAR(128) NOT NULL,
    description TEXT DEFAULT '',
    service VARCHAR(128) NOT NULL DEFAULT '*',
    metric VARCHAR(128) NOT NULL,
    condition VARCHAR(32) NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 300,
    severity VARCHAR(16) NOT NULL DEFAULT 'high',
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notification_channels JSONB NOT NULL DEFAULT '[]',
    labels JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_rules_org ON alert_rules(org_id);

-- Alert Events (fired alerts)
CREATE TABLE IF NOT EXISTS alert_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_id UUID NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    org_id UUID,
    service VARCHAR(128) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'firing',
    message TEXT DEFAULT '',
    value DOUBLE PRECISION NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alert_events_rule_status ON alert_events(rule_id, status);
CREATE INDEX IF NOT EXISTS idx_alert_events_org_started ON alert_events(org_id, started_at);

-- Runbook Executions
CREATE TABLE IF NOT EXISTS runbook_executions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID,
    runbook_id VARCHAR(128) NOT NULL,
    runbook_name VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    trigger VARCHAR(32) NOT NULL DEFAULT 'manual',
    triggered_by VARCHAR(256),
    variables JSONB NOT NULL DEFAULT '{}',
    results JSONB NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_runbook_exec_org_started ON runbook_executions(org_id, started_at);

-- Usage Records (billing/metering)
CREATE TABLE IF NOT EXISTS usage_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL,
    period VARCHAR(7) NOT NULL, -- YYYY-MM
    investigations INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(org_id, period)
);

CREATE INDEX IF NOT EXISTS idx_usage_org ON usage_records(org_id);
CREATE INDEX IF NOT EXISTS idx_usage_org_period ON usage_records(org_id, period);

-- Incidents (for incident management)
CREATE TABLE IF NOT EXISTS incidents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    external_id VARCHAR(128), -- PagerDuty/Opsgenie incident ID
    title VARCHAR(512) NOT NULL,
    description TEXT,
    severity VARCHAR(16) NOT NULL DEFAULT 'medium',
    status VARCHAR(32) NOT NULL DEFAULT 'triggered',
    service VARCHAR(128),
    source VARCHAR(64) DEFAULT 'manual',
    investigation_id UUID REFERENCES investigations(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incidents_org ON incidents(org_id);
CREATE INDEX IF NOT EXISTS idx_incidents_external ON incidents(external_id);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_org_created ON incidents(org_id, created_at);

-- Function to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
CREATE OR REPLACE TRIGGER update_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE TRIGGER update_alert_rules_updated_at
    BEFORE UPDATE ON alert_rules
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE TRIGGER update_usage_records_updated_at
    BEFORE UPDATE ON usage_records
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE TRIGGER update_incidents_updated_at
    BEFORE UPDATE ON incidents
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Row Level Security (RLS) Policies
-- Enable RLS on all tables
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE members ENABLE ROW LEVEL SECURITY;
ALTER TABLE investigations ENABLE ROW LEVEL SECURITY;
ALTER TABLE investigation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE runbook_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY;

-- RLS policies for organizations (users can only see their own org)
CREATE POLICY "Users can view their organization"
    ON organizations FOR SELECT
    USING (id = auth.jwt() ->> 'org_id'::text::uuid);

-- RLS policies for investigations
CREATE POLICY "Users can view their org investigations"
    ON investigations FOR SELECT
    USING (org_id = (auth.jwt() ->> 'org_id')::uuid);

CREATE POLICY "Users can insert investigations for their org"
    ON investigations FOR INSERT
    WITH CHECK (org_id = (auth.jwt() ->> 'org_id')::uuid);

-- RLS policies for incidents
CREATE POLICY "Users can view their org incidents"
    ON incidents FOR SELECT
    USING (org_id = (auth.jwt() ->> 'org_id')::uuid);

CREATE POLICY "Users can insert incidents for their org"
    ON incidents FOR INSERT
    WITH CHECK (org_id = (auth.jwt() ->> 'org_id')::uuid);

-- Service role bypass (for backend operations)
CREATE POLICY "Service role has full access to organizations"
    ON organizations FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service role has full access to investigations"
    ON investigations FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service role has full access to incidents"
    ON incidents FOR ALL
    USING (auth.role() = 'service_role');

-- Grant permissions
GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;
GRANT USAGE ON SCHEMA public TO anon;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;
