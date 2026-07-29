import { useState, useEffect } from "react";

import { Console } from "./components/Console";
import { CinematicLanding } from "./components/landing/CinematicLanding";
import { ApiDocs } from "./pages/ApiDocs";
import { Dashboard } from "./pages/Dashboard";
import { Billing } from "./pages/Billing";

type View = "landing" | "console" | "api-docs" | "dashboard" | "billing";

export default function App() {
  const [view, setView] = useState<View>("landing");

  // Handle URL params for billing success/cancel redirects
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('success') || params.has('canceled')) {
      setView('billing');
      // Clean up URL
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  return (
    <>
      {view === "landing" && (
        <CinematicLanding
          onLaunch={() => setView("console")}
          onDashboard={() => setView("dashboard")}
          onBilling={() => setView("billing")}
        />
      )}
      {view === "console" && (
        <Console
          onHome={() => setView("landing")}
          onDashboard={() => setView("dashboard")}
          onBilling={() => setView("billing")}
        />
      )}
      {view === "api-docs" && (
        <ApiDocs onBack={() => setView("landing")} />
      )}
      {view === "dashboard" && (
        <Dashboard
          onBack={() => setView("console")}
          onBilling={() => setView("billing")}
        />
      )}
      {view === "billing" && (
        <Billing onBack={() => setView("console")} />
      )}
    </>
  );
}
