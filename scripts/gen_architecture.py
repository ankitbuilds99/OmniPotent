#!/usr/bin/env python3
"""Generate the isometric architecture diagram used in the README.

The diagram is committed as SVG, but it is *generated* rather than hand-drawn:
six stacked layers with four nodes each is 200-odd polygons, and hand-editing
coordinates in XML is how diagrams drift out of sync with the system they
describe. Edit ``LAYERS`` below and re-run; the geometry follows.

    python scripts/gen_architecture.py

Writes ``docs/assets/architecture-3d-light.svg`` and ``-dark.svg``. The README
selects between them with a ``<picture>`` element so the diagram stays legible
in both GitHub themes.

Projection note: this is a *flattened* isometric (the vertical scale is 0.30
rather than the true 0.50). True isometric makes each slab (W+D)/2 tall on
screen, which for six layers is a 2400px image nobody scrolls through. At 0.30
the stack fits in half that and the layers still read as planes seen from above.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- projection

KX = 0.86  # horizontal scale: how far one unit of x/y moves sideways
KY = 0.30  # vertical scale: how far one unit of x/y moves down (see note above)


def iso(x: float, y: float, z: float) -> tuple[float, float]:
    """Project a point in model space onto the screen."""
    return ((x - y) * KX, (x + y) * KY - z)


def pts(*corners: tuple[float, float]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in corners)


# ------------------------------------------------------------------ geometry

W = 600.0  # slab extent along +x (runs down-right on screen)
D = 150.0  # slab extent along +y (runs down-left on screen)
T = 14.0  # slab thickness
GAP = 170.0  # screen-vertical distance between consecutive slabs

NW = ND = 80.0  # node footprint
NH = 26.0  # node height
NODE_Y = 35.0  # inset of the node row from the slab's back edge
NODE_X0 = 26.0  # inset of the first node from the slab's left edge
NODE_DX = 160.0  # spacing between nodes along +x


# ---------------------------------------------------------------- the system


@dataclass
class Layer:
    key: str
    title: str
    caption: str
    accent: str
    nodes: list[str] = field(default_factory=list)


LAYERS: list[Layer] = [
    Layer(
        key="edge",
        title="EDGE",
        caption="Authenticate, throttle, resolve the tenant",
        accent="#38bdf8",
        nodes=["OAuth2 / JWT", "API key + HMAC", "Rate limiter", "RBAC + tenancy"],
    ),
    Layer(
        key="orchestration",
        title="ORCHESTRATION",
        caption="Score the ask, pick a model, drive the graph",
        accent="#6366f1",
        nodes=["Complexity score", "Adaptive router", "LangGraph FSM", "Checkpoints"],
    ),
    Layer(
        key="agents",
        title="AGENTS",
        caption="Gather evidence in parallel, propose one fix",
        accent="#10b981",
        nodes=["Investigator", "Remediation", "Reviewer", "Coordinator"],
    ),
    Layer(
        key="safety",
        title="SAFETY GATES",
        caption="Every gate passes, or a human approves it",
        accent="#f59e0b",
        nodes=["Confidence", "Policy / Rego", "Digital twin", "Approval"],
    ),
    Layer(
        key="integration",
        title="INTEGRATION",
        caption="27 MCP servers reach the world outside",
        accent="#f43f5e",
        nodes=["MCP servers", "Connectors", "ITSM + chat", "Cloud + K8s"],
    ),
    Layer(
        key="persistence",
        title="PERSISTENCE",
        caption="State, cache, and a tamper-evident trail",
        accent="#a855f7",
        nodes=["PostgreSQL", "Redis", "Object store", "Hash-chained audit"],
    ),
]


# ------------------------------------------------------------------- theming


@dataclass
class Theme:
    name: str
    bg: str
    surface: str
    side_l: str
    side_r: str
    node: str
    node_side: str
    title: str
    caption: str
    label: str
    rule: str
    spine: str
    top_op: str
    glow_op: str


LIGHT = Theme(
    name="light",
    bg="#ffffff",
    surface="#f1f5f9",
    side_l="#cbd5e1",
    side_r="#94a3b8",
    node="#ffffff",
    node_side="#cbd5e1",
    title="#0f172a",
    caption="#64748b",
    label="#1e293b",
    rule="#cbd5e1",
    spine="#94a3b8",
    top_op="0.20",
    glow_op="0.10",
)

DARK = Theme(
    name="dark",
    bg="#0b1120",
    surface="#1e293b",
    side_l="#172033",
    side_r="#0f172a",
    node="#334155",
    node_side="#1e293b",
    title="#f8fafc",
    caption="#94a3b8",
    label="#e2e8f0",
    rule="#334155",
    spine="#475569",
    top_op="0.26",
    glow_op="0.16",
)


# ------------------------------------------------------------------ drawing


def slab(z: float, accent: str, th: Theme) -> str:
    """One layer platform: a top face plus the two faces the viewer can see."""
    top = [iso(0, 0, z), iso(W, 0, z), iso(W, D, z), iso(0, D, z)]
    # +x face, catching the light from the right
    right = [iso(W, 0, z), iso(W, D, z), iso(W, D, z - T), iso(W, 0, z - T)]
    # +y face, turned away from it
    left = [iso(0, D, z), iso(W, D, z), iso(W, D, z - T), iso(0, D, z - T)]
    return f"""
    <polygon points="{pts(*left)}" fill="{th.side_l}"/>
    <polygon points="{pts(*right)}" fill="{th.side_r}"/>
    <polygon points="{pts(*top)}" fill="{th.surface}"/>
    <polygon points="{pts(*top)}" fill="{accent}" opacity="{th.top_op}"/>
    <polygon points="{pts(*top)}" fill="url(#sheen)"/>
    <polygon points="{pts(*top)}" fill="none" stroke="{accent}" stroke-width="1.6" opacity="0.55"/>"""


def node(x: float, y: float, z: float, accent: str, th: Theme) -> str:
    """A small cuboid standing on a slab."""
    zt = z + NH
    top = [iso(x, y, zt), iso(x + NW, y, zt), iso(x + NW, y + ND, zt), iso(x, y + ND, zt)]
    right = [
        iso(x + NW, y, zt),
        iso(x + NW, y + ND, zt),
        iso(x + NW, y + ND, z),
        iso(x + NW, y, z),
    ]
    left = [
        iso(x, y + ND, zt),
        iso(x + NW, y + ND, zt),
        iso(x + NW, y + ND, z),
        iso(x, y + ND, z),
    ]
    return f"""
    <polygon points="{pts(*left)}" fill="{th.node_side}"/>
    <polygon points="{pts(*right)}" fill="{accent}" opacity="0.75"/>
    <polygon points="{pts(*top)}" fill="{th.node}"/>
    <polygon points="{pts(*top)}" fill="{accent}" opacity="0.45"/>
    <polygon points="{pts(*top)}" fill="none" stroke="{accent}" stroke-width="1.2" opacity="0.9"/>"""


def build(th: Theme) -> str:
    body: list[str] = []

    # Painter's algorithm: the bottom slab is furthest away, so it goes down
    # first and every layer above it paints over its back edge. That overlap is
    # what makes the six planes read as a stack rather than six loose diagrams.
    for i in reversed(range(len(LAYERS))):
        layer = LAYERS[i]
        z = -i * GAP
        body.append(f'  <g id="layer-{layer.key}">')
        body.append(slab(z, layer.accent, th))

        # Nodes, back to front, so the nearer cuboid occludes the further one.
        for j, name in enumerate(layer.nodes):
            nx = NODE_X0 + j * NODE_DX
            body.append(node(nx, NODE_Y, z, layer.accent, th))
            lx, ly = iso(nx + NW / 2, NODE_Y + ND / 2, z + NH)
            body.append(
                f'    <text x="{lx:.1f}" y="{ly - 12:.1f}" text-anchor="middle" '
                f'font-size="15" font-weight="600" fill="{th.label}">'
                f"{html.escape(name)}</text>"
            )

        # Title block, parked to the left of the slab's near-left corner and
        # joined to it by a rule, so long titles never collide with the art.
        cx, cy = iso(0, D, z)
        body.append(
            f'    <line x1="-150" y1="{cy:.1f}" x2="{cx - 8:.1f}" y2="{cy:.1f}" '
            f'stroke="{th.rule}" stroke-width="1.5"/>'
        )
        body.append(
            f'    <circle cx="-150" cy="{cy:.1f}" r="5" fill="{layer.accent}"/>'
        )
        body.append(
            f'    <text x="-166" y="{cy - 6:.1f}" text-anchor="end" font-size="21" '
            f'font-weight="700" letter-spacing="1.2" fill="{th.title}">'
            f"{html.escape(layer.title)}</text>"
        )
        body.append(
            f'    <text x="-166" y="{cy + 16:.1f}" text-anchor="end" font-size="14" '
            f'fill="{th.caption}">{html.escape(layer.caption)}</text>'
        )
        body.append("  </g>")

    # The spine: one request falling through every layer in order. Anchored to
    # each slab's far-right corner so it tracks the stack instead of floating.
    top_y = iso(W, 0, 0)[1]
    bot_y = iso(W, 0, -(len(LAYERS) - 1) * GAP)[1] + 26
    sx = 585.0
    body.append(
        f'  <line x1="{sx}" y1="{top_y:.1f}" x2="{sx}" y2="{bot_y:.1f}" '
        f'stroke="{th.spine}" stroke-width="2" stroke-dasharray="7 6" '
        f'marker-end="url(#arrow)"/>'
    )
    for i, layer in enumerate(LAYERS):
        y = iso(W, 0, -i * GAP)[1]
        body.append(
            f'  <line x1="{iso(W, 0, -i * GAP)[0]:.1f}" y1="{y:.1f}" x2="{sx}" '
            f'y2="{y:.1f}" stroke="{th.rule}" stroke-width="1.2"/>'
        )
        body.append(f'  <circle cx="{sx}" cy="{y:.1f}" r="4.5" fill="{layer.accent}"/>')
    body.append(
        f'  <text x="{sx}" y="{top_y - 18:.1f}" text-anchor="middle" font-size="13" '
        f'font-weight="600" letter-spacing="0.6" fill="{th.caption}">one request</text>'
    )

    inner = "\n".join(body)
    vb = "-500 -58 1190 1180"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}"
     width="1190" height="1180" role="img"
     aria-label="OmniPotent architecture: six stacked layers, from edge authentication
     down through orchestration, agents, safety gates, integrations and persistence.">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
            markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{th.spine}"/>
    </marker>
    <linearGradient id="sheen" x1="0" y1="0" x2="0.45" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="{th.glow_op}"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0.05"/>
    </linearGradient>
  </defs>
  <rect x="-500" y="-58" width="1190" height="1180" fill="{th.bg}"/>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter,
            "Helvetica Neue", Arial, sans-serif; }}
  </style>
{inner}
</svg>
"""


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "docs" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    for theme in (LIGHT, DARK):
        path = out / f"architecture-3d-{theme.name}.svg"
        path.write_text(build(theme), encoding="utf-8")
        print(f"wrote {path.relative_to(path.parents[2])}")


if __name__ == "__main__":
    main()
