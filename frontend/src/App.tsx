import { useState } from "react";

import { Console } from "./components/Console";
import { CinematicLanding } from "./components/landing/CinematicLanding";
import { ApiDocs } from "./pages/ApiDocs";

type View = "landing" | "console" | "api-docs";

export default function App() {
  const [view, setView] = useState<View>("landing");

  return (
    <>
      {view === "landing" && (
        <CinematicLanding onLaunch={() => setView("console")} />
      )}
      {view === "console" && (
        <Console onHome={() => setView("landing")} />
      )}
      {view === "api-docs" && (
        <ApiDocs onBack={() => setView("landing")} />
      )}
    </>
  );
}
