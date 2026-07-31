export interface Theme {
  id: string;
  name: string;
  description: string;
  // [from, to] gradient used for the swatch preview
  swatch: [string, string];
}

export const THEMES: Theme[] = [
  {
    id: "cosmic",
    name: "Cosmic",
    description: "Deep space — purple & blue accents",
    swatch: ["#7c5cff", "#6491ff"],
  },
  {
    id: "midnight",
    name: "Midnight",
    description: "Dark & sleek with blue accents",
    swatch: ["#2f7fe0", "#4d9fff"],
  },
  {
    id: "forest",
    name: "Forest",
    description: "Deep green tones, emerald accents",
    swatch: ["#10b981", "#34d399"],
  },
  {
    id: "sunset",
    name: "Sunset",
    description: "Warm sunset hues, orange accents",
    swatch: ["#f0653e", "#ff8a4c"],
  },
  {
    id: "ocean",
    name: "Ocean",
    description: "Deep sea vibes, cyan & teal",
    swatch: ["#06b6d4", "#22d3ee"],
  },
  {
    id: "rose",
    name: "Rose",
    description: "Elegant pink & magenta tones",
    swatch: ["#ec4899", "#f472b6"],
  },
  {
    id: "aurora",
    name: "Aurora",
    description: "Northern lights — green & purple",
    swatch: ["#8b5cf6", "#22c55e"],
  },
  {
    id: "noir",
    name: "Noir",
    description: "Pure black with white accents",
    swatch: ["#ffffff", "#a1a1aa"],
  },
  {
    id: "cyberpunk",
    name: "Cyberpunk",
    description: "Neon yellow & hot pink",
    swatch: ["#facc15", "#f43f5e"],
  },
  {
    id: "light",
    name: "Light",
    description: "Clean & minimal light theme",
    swatch: ["#3b6fed", "#7c5cff"],
  },
];

export const DEFAULT_THEME = "cosmic";
