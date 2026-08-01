import { useState, useEffect } from "react";

import { Console } from "./components/Console";
import { CinematicLanding } from "./components/landing/CinematicLanding";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { ApiDocs } from "./pages/ApiDocs";
import { Dashboard } from "./pages/Dashboard";
import { Billing } from "./pages/Billing";
import { Login } from "./pages/Login";
import { AuthCallback } from "./pages/AuthCallback";
import { Profile } from "./pages/Profile";
import { History } from "./pages/History";
import { StatusPage } from "./pages/StatusPage";
import { Team } from "./pages/Team";
import { Incidents } from "./pages/Incidents";
import { OnCall } from "./pages/OnCall";
import { ServiceMap } from "./pages/ServiceMap";
import { NotificationPreferences } from "./pages/NotificationPreferences";
import { Postmortem } from "./pages/Postmortem";
import { Analytics } from "./pages/Analytics";
import { SLOTracker } from "./pages/SLOTracker";
import { RunbookBuilder } from "./pages/RunbookBuilder";
import { IntegrationMarketplace } from "./pages/IntegrationMarketplace";
import { KnowledgeBase } from "./pages/KnowledgeBase";

type View = "landing" | "login" | "auth-callback" | "console" | "api-docs" | "dashboard" | "billing" | "profile" | "history" | "status" | "team" | "incidents" | "oncall" | "servicemap" | "notifications" | "postmortem" | "analytics" | "slo" | "runbooks" | "onboarding" | "integrations" | "knowledge";

export default function App() {
  const [view, setView] = useState<View>("landing");

  // Handle URL routing for auth callbacks and billing redirects
  useEffect(() => {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);

    // Handle OAuth callback
    if (path === '/auth/callback' || path.includes('auth/callback')) {
      setView('auth-callback');
      return;
    }

    // Handle billing success/cancel redirects
    if (params.has('success') || params.has('canceled')) {
      setView('billing');
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  return (
    <>
      {view === "landing" && (
        <CinematicLanding
          onLaunch={() => setView("login")}
          onDashboard={() => setView("dashboard")}
          onBilling={() => setView("billing")}
        />
      )}
      {view === "login" && (
        <Login
          onBack={() => setView("landing")}
          onSuccess={() => {
            const hasCompletedOnboarding = localStorage.getItem('onboarding_completed');
            if (hasCompletedOnboarding) {
              setView("console");
            } else {
              setView("onboarding");
            }
          }}
        />
      )}
      {view === "auth-callback" && (
        <AuthCallback
          onSuccess={() => {
            window.history.replaceState({}, '', '/');
            setView("console");
          }}
          onError={() => {
            window.history.replaceState({}, '', '/');
            setView("login");
          }}
        />
      )}
      {view === "console" && (
        <Console
          onHome={() => setView("landing")}
          onDashboard={() => setView("dashboard")}
          onBilling={() => setView("billing")}
          onLogout={() => setView("landing")}
          onProfile={() => setView("profile")}
          onHistory={() => setView("history")}
          onStatus={() => setView("status")}
          onTeam={() => setView("team")}
        />
      )}
      {view === "api-docs" && (
        <ApiDocs onBack={() => setView("landing")} />
      )}
      {view === "dashboard" && (
        <Dashboard
          onBack={() => setView("console")}
          onBilling={() => setView("billing")}
          onIncidents={() => setView("incidents")}
          onOnCall={() => setView("oncall")}
          onServiceMap={() => setView("servicemap")}
          onNotifications={() => setView("notifications")}
          onPostmortem={() => setView("postmortem")}
          onAnalytics={() => setView("analytics")}
          onSLO={() => setView("slo")}
          onRunbooks={() => setView("runbooks")}
          onIntegrations={() => setView("integrations")}
          onKnowledge={() => setView("knowledge")}
        />
      )}
      {view === "billing" && (
        <Billing onBack={() => setView("console")} />
      )}
      {view === "profile" && (
        <Profile
          onBack={() => setView("console")}
          onLogout={() => setView("landing")}
        />
      )}
      {view === "history" && (
        <History
          onBack={() => setView("console")}
          onViewInvestigation={(threadId) => {
            console.log('Continue investigation:', threadId)
            setView("console")
          }}
        />
      )}
      {view === "status" && (
        <StatusPage onBack={() => setView("console")} />
      )}
      {view === "team" && (
        <Team onBack={() => setView("console")} />
      )}
      {view === "incidents" && (
        <Incidents onBack={() => setView("dashboard")} />
      )}
      {view === "oncall" && (
        <OnCall onBack={() => setView("dashboard")} />
      )}
      {view === "servicemap" && (
        <ServiceMap onBack={() => setView("dashboard")} />
      )}
      {view === "notifications" && (
        <NotificationPreferences onBack={() => setView("dashboard")} />
      )}
      {view === "postmortem" && (
        <Postmortem onBack={() => setView("dashboard")} />
      )}
      {view === "analytics" && (
        <Analytics onBack={() => setView("dashboard")} />
      )}
      {view === "slo" && (
        <SLOTracker onBack={() => setView("dashboard")} />
      )}
      {view === "runbooks" && (
        <RunbookBuilder onBack={() => setView("dashboard")} />
      )}
      {view === "onboarding" && (
        <OnboardingWizard
          onComplete={() => {
            localStorage.setItem('onboarding_completed', 'true');
            setView("console");
          }}
          onSkip={() => {
            localStorage.setItem('onboarding_completed', 'true');
            setView("console");
          }}
        />
      )}
      {view === "integrations" && (
        <IntegrationMarketplace onBack={() => setView("dashboard")} />
      )}
      {view === "knowledge" && (
        <KnowledgeBase onBack={() => setView("dashboard")} />
      )}
    </>
  );
}
