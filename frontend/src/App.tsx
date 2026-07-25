import { useState } from "react";

import { Console } from "./components/Console";
import { CinematicLanding } from "./components/landing/CinematicLanding";

export default function App() {
  const [view, setView] = useState<"landing" | "console">("landing");

  return (
    <>
      {view === "landing" ? (
        <CinematicLanding onLaunch={() => setView("console")} />
      ) : (
        <Console onHome={() => setView("landing")} />
      )}
    </>
  );
}
