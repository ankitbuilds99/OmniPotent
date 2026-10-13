import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { Console } from "./components/Console";
import { OnboardingWizard } from "./components/OnboardingWizard";
const ApiDocs = lazy(() => import("./pages/ApiDocs").then((m) => ({ default: m.ApiDocs })));
const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const Billing = lazy(() => import("./pages/Billing").then((m) => ({ default: m.Billing })));
const Login = lazy(() => import("./pages/Login").then((m) => ({ default: m.Login })));
const AuthCallback = lazy(() => import("./pages/AuthCallback").then((m) => ({ default: m.AuthCallback })));
const Profile = lazy(() => import("./pages/Profile").then((m) => ({ default: m.Profile })));
const History = lazy(() => import("./pages/History").then((m) => ({ default: m.History })));
const StatusPage = lazy(() => import("./pages/StatusPage").then((m) => ({ default: m.StatusPage })));
const Team = lazy(() => import("./pages/Team").then((m) => ({ default: m.Team })));
const AcceptInvite = lazy(() => import("./pages/AcceptInvite").then((m) => ({ default: m.AcceptInvite })));
const Incidents = lazy(() => import("./pages/Incidents").then((m) => ({ default: m.Incidents })));
const OnCall = lazy(() => import("./pages/OnCall").then((m) => ({ default: m.OnCall })));
const ServiceMap = lazy(() => import("./pages/ServiceMap").then((m) => ({ default: m.ServiceMap })));
const NotificationPreferences = lazy(() => import("./pages/NotificationPreferences").then((m) => ({ default: m.NotificationPreferences })));
const Postmortem = lazy(() => import("./pages/Postmortem").then((m) => ({ default: m.Postmortem })));
const Analytics = lazy(() => import("./pages/Analytics").then((m) => ({ default: m.Analytics })));
const SLOTracker = lazy(() => import("./pages/SLOTracker").then((m) => ({ default: m.SLOTracker })));
const RunbookBuilder = lazy(() => import("./pages/RunbookBuilder").then((m) => ({ default: m.RunbookBuilder })));
const IntegrationMarketplace = lazy(() => import("./pages/IntegrationMarketplace").then((m) => ({ default: m.IntegrationMarketplace })));
const KnowledgeBase = lazy(() => import("./pages/KnowledgeBase").then((m) => ({ default: m.KnowledgeBase })));
const PricingPage = lazy(() => import("./pages/PricingPage").then((m) => ({ default: m.PricingPage })));
const CapabilitiesPage = lazy(() => import("./pages/CapabilitiesPage").then((m) => ({ default: m.CapabilitiesPage })));
const CinematicLanding = lazy(() => import("./components/landing/CinematicLanding").then((m) => ({ default: m.CinematicLanding })));
import { isPublicRoute, PENDING_INVITATION_KEY } from "./lib/navigation";
import { isAuthenticated } from "./lib/authToken";

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
  const location = useLocation();

  // Backend authorization remains authoritative. This client-side gate only
  // prevents an unauthenticated browser from rendering customer workspace
  // screens before its first API request; it never grants access by itself.
  if (!isPublicRoute(location.pathname) && !isAuthenticated()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  // Where a fresh login lands: the wizard until it has been completed once.
  const afterLogin = () => {
    // Somebody who arrived from an invitation link came here to join a team,
    // not to see the console. Take them back to the invitation they clicked.
    const pendingInvitation = localStorage.getItem(PENDING_INVITATION_KEY);
    if (pendingInvitation) {
      localStorage.removeItem(PENDING_INVITATION_KEY);
      navigate(`/invite/${pendingInvitation}`, { replace: true });
      return;
    }
    const done = localStorage.getItem("onboarding_completed");
    navigate(done ? "/console" : "/onboarding", { replace: true });
  };

  const finishOnboarding = () => {
    localStorage.setItem("onboarding_completed", "true");
    navigate("/console", { replace: true });
  };

  return (
    <Suspense fallback={<div role="status" aria-live="polite" className="route-loading">Loading workspace…</div>}>
    <Routes>
      {/* Open the application console by default. */}
      <Route path="/" element={<Navigate to="/console" replace />} />
      {/* Keep the old landing URL from trapping users on the marketing page. */}
      <Route path="/landing" element={<Navigate to="/console" replace />} />
      <Route
        path="/marketing"
        element={
          <CinematicLanding
            onLaunch={() => navigate("/login")}
            onDashboard={() => navigate("/dashboard")}
            onBilling={() => navigate("/settings/billing")}
          />
        }
      />
      <Route path="/pricing" element={<PricingPage onLaunch={() => navigate("/login")} />} />
      <Route path="/capabilities" element={<CapabilitiesPage onLaunch={() => navigate("/login")} />} />
      <Route
        path="/login"
        element={<Login onBack={() => navigate("/marketing")} onSuccess={afterLogin} />}
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
            onHome={() => navigate("/marketing")}
            onDashboard={() => navigate("/dashboard")}
            onBilling={() => navigate("/settings/billing")}
            onLogout={() => navigate("/marketing", { replace: true })}
            onProfile={() => navigate("/profile")}
            onHistory={() => navigate("/history")}
            onStatus={() => navigate("/status")}
            onTeam={() => navigate("/team")}
          />
        }
      />
      <Route path="/api-docs" element={<ApiDocs onBack={() => navigate("/marketing")} />} />
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
            onLogout={() => navigate("/marketing", { replace: true })}
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
      {/* The address Team.tsx puts in every invitation email. Without a route
          here the invitee landed on the marketing page and the invitation sat
          pending until it expired. */}
      <Route path="/invite/:invitationId" element={<AcceptInvite />} />
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
      <Route path="*" element={<Navigate to="/console" replace />} />
    </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
