import { useState } from "react";

import { Console } from "./components/Console";
import { CinematicLanding } from "./components/landing/CinematicLanding";
import { ApiDocs } from "./pages/ApiDocs";
import { Dashboard } from "./pages/Dashboard";

type View = "landing" | "console" | "api-docs" | "dashboard";

export default function App() {
  const [view, setView] = useState<View>("landing");

  return (
    <>
      {view === "landing" && (
        <CinematicLanding onLaunch={() => setView("console")} onDashboard={() => setView("dashboard")} />
      )}
      {view === "console" && (
        <Console onHome={() => setView("landing")} onDashboard={() => setView("dashboard")} />
      )}
      {view === "api-docs" && (
        <ApiDocs onBack={() => setView("landing")} />
      )}
      {view === "dashboard" && (
        <Dashboard onBack={() => setView("console")} />
      )}
    </>
  );
}
