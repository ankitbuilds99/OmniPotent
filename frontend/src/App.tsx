import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";

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

// Every page used to be a value of a `view` useState, so nothing had a URL:
// reload dropped you back on the landing page, no screen could be linked to or
// bookmarked, and the browser Back button left the app entirely.
//
// Paths avoid the backend's top-level API segments, which win at the origin —
// hence `/on-call` (not `/oncall`), `/marketplace` (not `/integrations`) and
// `/settings/billing` (not `/billing`, and it is also Stripe's default
// success_url). `/auth/callback` is the one exception: it sits under an API
// segment, so `app/api/main.py` allows it through to the SPA by name.

function AppRoutes() {
  const navigate = useNavigate();

  // Where a fresh login lands: the wizard until it has been completed once.
  const afterLogin = () => {
    const done = localStorage.getItem("onboarding_completed");
    navigate(done ? "/console" : "/onboarding", { replace: true });
  };

  const finishOnboarding = () => {
    localStorage.setItem("onboarding_completed", "true");
    navigate("/console", { replace: true });
  };

  return (
    <Routes>
      <Route
        path="/"
        element={
          <CinematicLanding
            onLaunch={() => navigate("/login")}
            onDashboard={() => navigate("/dashboard")}
            onBilling={() => navigate("/settings/billing")}
          />
        }
      />
      <Route
        path="/login"
        element={<Login onBack={() => navigate("/")} onSuccess={afterLogin} />}
      />
      <Route
        path="/auth/callback"
        element={
          <AuthCallback
            onSuccess={() => navigate("/console", { replace: true })}
            onError={() => navigate("/login", { replace: true })}
          />
        }
      />
      <Route
        path="/onboarding"
        element={
          <OnboardingWizard onComplete={finishOnboarding} onSkip={finishOnboarding} />
        }
      />
      <Route
        path="/console"
        element={
          <Console
            onHome={() => navigate("/")}
            onDashboard={() => navigate("/dashboard")}
            onBilling={() => navigate("/settings/billing")}
            onLogout={() => navigate("/", { replace: true })}
            onProfile={() => navigate("/profile")}
            onHistory={() => navigate("/history")}
            onStatus={() => navigate("/status")}
            onTeam={() => navigate("/team")}
          />
        }
      />
      <Route path="/api-docs" element={<ApiDocs onBack={() => navigate("/")} />} />
      <Route
        path="/dashboard"
        element={
          <Dashboard
            onBack={() => navigate("/console")}
            onBilling={() => navigate("/settings/billing")}
            onIncidents={() => navigate("/incidents")}
            onOnCall={() => navigate("/on-call")}
            onServiceMap={() => navigate("/service-map")}
            onNotifications={() => navigate("/notifications")}
            onPostmortem={() => navigate("/postmortem")}
            onAnalytics={() => navigate("/analytics")}
            onSLO={() => navigate("/slo")}
            onRunbooks={() => navigate("/runbooks")}
            onIntegrations={() => navigate("/marketplace")}
            onKnowledge={() => navigate("/knowledge")}
          />
        }
      />
      <Route
        path="/settings/billing"
        element={<Billing onBack={() => navigate("/console")} />}
      />
      <Route
        path="/profile"
        element={
          <Profile
            onBack={() => navigate("/console")}
            onLogout={() => navigate("/", { replace: true })}
          />
        }
      />
      <Route
        path="/history"
        element={
          <History
            onBack={() => navigate("/console")}
            onViewInvestigation={(threadId) => navigate(`/console?thread=${threadId}`)}
          />
        }
      />
      <Route path="/status" element={<StatusPage onBack={() => navigate("/console")} />} />
      <Route path="/team" element={<Team onBack={() => navigate("/console")} />} />
      <Route
        path="/incidents"
        element={<Incidents onBack={() => navigate("/dashboard")} />}
      />
      <Route path="/on-call" element={<OnCall onBack={() => navigate("/dashboard")} />} />
      <Route
        path="/service-map"
        element={<ServiceMap onBack={() => navigate("/dashboard")} />}
      />
      <Route
        path="/notifications"
        element={<NotificationPreferences onBack={() => navigate("/dashboard")} />}
      />
      <Route
        path="/postmortem"
        element={<Postmortem onBack={() => navigate("/dashboard")} />}
      />
      <Route
        path="/analytics"
        element={<Analytics onBack={() => navigate("/dashboard")} />}
      />
      <Route path="/slo" element={<SLOTracker onBack={() => navigate("/dashboard")} />} />
      <Route
        path="/runbooks"
        element={<RunbookBuilder onBack={() => navigate("/dashboard")} />}
      />
      <Route
        path="/marketplace"
        element={<IntegrationMarketplace onBack={() => navigate("/dashboard")} />}
      />
      <Route
        path="/knowledge"
        element={<KnowledgeBase onBack={() => navigate("/dashboard")} />}
      />
      {/* The server serves index.html for any unknown non-API path, so an
          unrecognised URL arrives here rather than as a 404. Send it home. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
