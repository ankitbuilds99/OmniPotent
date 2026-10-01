# Approved marketing-section removal

The `/marketing` composition no longer mounts the `GlobalMap` browser-to-cloud
latency section. It was the only consumer of `GlobalMap.tsx`, so the component
and its section-specific Three.js scene, observers, interval polling, and
browser `HEAD` probes were removed together.

There was no `id`, navbar item, or other public navigation destination for this
section. Existing anchors (`#features`, `#pipeline`, `#dashboard`, `#pricing`,
`#docs`, and `#blog`) remain unchanged, so no deep-link behavior is affected.

Three.js and `@react-three/drei` remain installed because the remaining landing
hero/footer visualizations still use them.
