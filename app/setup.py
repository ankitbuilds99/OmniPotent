"""Interactive setup wizard for DevOps Copilot.

Usage:
    copilot-setup              # interactive wizard
    copilot-setup --check      # check current connection status
    copilot-setup --reset      # reset to demo mode

Guides users through connecting their infrastructure services step-by-step.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box

console = Console()

# Project root where .env lives
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

LOGO = """
[bold cyan]╔═══════════════════════════════════════════════════════════╗
║                                                            ║
║     ██████╗ ███████╗██╗   ██╗ ██████╗ ██████╗ ███████╗    ║
║     ██╔══██╗██╔════╝██║   ██║██╔═══██╗██╔══██╗██╔════╝    ║
║     ██║  ██║█████╗  ██║   ██║██║   ██║██████╔╝███████╗    ║
║     ██║  ██║██╔══╝  ╚██╗ ██╔╝██║   ██║██╔═══╝ ╚════██║    ║
║     ██████╔╝███████╗ ╚████╔╝ ╚██████╔╝██║     ███████║    ║
║     ╚═════╝ ╚══════╝  ╚═══╝   ╚═════╝ ╚═╝     ╚══════╝    ║
║                                                            ║
║              [bold white]C O P I L O T[/bold white]   [dim]Setup Wizard[/dim]              ║
║                                                            ║
╚═══════════════════════════════════════════════════════════╝[/bold cyan]
"""

SERVICES = {
    "llm": {
        "name": "LLM Provider",
        "description": "AI model for investigation",
        "required": True,
        "options": {
            "anthropic": {
                "name": "Anthropic (Claude)",
                "vars": ["ANTHROPIC_API_KEY"],
                "provider_value": "anthropic",
            },
            "openai": {
                "name": "OpenAI (GPT-4)",
                "vars": ["OPENAI_API_KEY"],
                "provider_value": "openai",
            },
            "gemini": {
                "name": "Google (Gemini)",
                "vars": ["GEMINI_API_KEY"],
                "provider_value": "gemini",
            },
            "groq": {
                "name": "Groq (Fast Llama)",
                "vars": ["GROQ_API_KEY"],
                "provider_value": "groq",
            },
            "deepseek": {
                "name": "DeepSeek",
                "vars": ["DEEPSEEK_API_KEY"],
                "provider_value": "deepseek",
            },
        },
    },
    "datadog": {
        "name": "Datadog",
        "description": "Logs, metrics, anomaly detection",
        "required": False,
        "vars": ["DD_API_KEY", "DD_APP_KEY"],
        "optional_vars": ["DD_SITE"],
        "defaults": {"DD_SITE": "datadoghq.com"},
    },
    "pagerduty": {
        "name": "PagerDuty",
        "description": "Incident management & alerts",
        "required": False,
        "vars": ["PAGERDUTY_API_TOKEN"],
        "optional_vars": ["PAGERDUTY_FROM_EMAIL"],
    },
    "kubernetes": {
        "name": "Kubernetes",
        "description": "Cluster monitoring & management",
        "required": False,
        "vars": ["KUBE_CONFIG_PATH"],
        "optional_vars": ["KUBE_NAMESPACE"],
        "defaults": {"KUBE_NAMESPACE": "default"},
        "hints": {"KUBE_CONFIG_PATH": "~/.kube/config"},
    },
    "sentry": {
        "name": "Sentry",
        "description": "Error tracking",
        "required": False,
        "vars": ["SENTRY_API_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT"],
    },
    "github": {
        "name": "GitHub",
        "description": "Code repository & PR creation",
        "required": False,
        "vars": ["GITHUB_TOKEN", "GITHUB_REPO"],
        "hints": {"GITHUB_REPO": "owner/repo-name"},
    },
    "traces": {
        "name": "Distributed Tracing",
        "description": "Jaeger/Zipkin traces",
        "required": False,
        "vars": ["TRACES_API_URL"],
        "hints": {"TRACES_API_URL": "http://jaeger:16686"},
    },
    "slack": {
        "name": "Slack",
        "description": "Notifications & approval buttons",
        "required": False,
        "vars": ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL"],
        "hints": {"SLACK_CHANNEL": "#incidents"},
    },
}


def load_env() -> dict[str, str]:
    """Load existing .env file as a dict."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def save_env(env: dict[str, str]) -> None:
    """Save environment variables to .env file, preserving comments from example."""
    lines = []
    if ENV_EXAMPLE.exists():
        example_content = ENV_EXAMPLE.read_text()
        for line in example_content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=")[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                else:
                    lines.append(line)
            else:
                lines.append(line)
    else:
        for key, value in env.items():
            lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n")


def get_connection_status() -> dict[str, str]:
    """Check which services are connected."""
    env = {**os.environ, **load_env()}
    status = {}

    # LLM
    provider = env.get("COPILOT_PROVIDER", "anthropic")
    llm_keys = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    key = llm_keys.get(provider, "ANTHROPIC_API_KEY")
    status["llm"] = "connected" if env.get(key) else "not_configured"

    # Other services
    service_keys = {
        "datadog": ["DD_API_KEY", "DD_APP_KEY"],
        "pagerduty": ["PAGERDUTY_API_TOKEN"],
        "kubernetes": ["KUBE_CONFIG_PATH"],
        "sentry": ["SENTRY_API_TOKEN"],
        "github": ["GITHUB_TOKEN"],
        "traces": ["TRACES_API_URL"],
        "slack": ["SLACK_BOT_TOKEN"],
    }

    for service, keys in service_keys.items():
        if all(env.get(k) for k in keys):
            status[service] = "connected"
        else:
            status[service] = "demo_mode"

    return status


def show_status() -> None:
    """Display current connection status."""
    console.print(LOGO)

    status = get_connection_status()
    env = {**os.environ, **load_env()}

    table = Table(title="Connection Status", box=box.ROUNDED, border_style="cyan")
    table.add_column("Service", style="bold")
    table.add_column("Status")
    table.add_column("Mode")

    icons = {
        "connected": "[green]✓ Connected[/green]",
        "demo_mode": "[yellow]◐ Demo Mode[/yellow]",
        "not_configured": "[red]✗ Not Configured[/red]",
    }

    for key, svc in SERVICES.items():
        svc_status = status.get(key, "demo_mode")
        mode = ""
        if key == "llm" and svc_status == "connected":
            provider = env.get("COPILOT_PROVIDER", "anthropic")
            mode = f"[dim]{provider}[/dim]"
        elif svc_status == "demo_mode":
            mode = "[dim]Using fixtures[/dim]"
        elif svc_status == "connected":
            mode = "[dim]Live API[/dim]"

        table.add_row(svc["name"], icons[svc_status], mode)

    console.print(table)
    console.print()

    # Summary
    connected = sum(1 for s in status.values() if s == "connected")
    total = len(status)

    if status["llm"] == "not_configured":
        console.print("[red]⚠ LLM provider not configured. Run [bold]copilot-setup[/bold] to get started.[/red]")
    elif connected == total:
        console.print("[green]✓ All services connected! You're ready to investigate real incidents.[/green]")
    else:
        console.print(f"[cyan]ℹ {connected}/{total} services connected. Others running in demo mode.[/cyan]")


def setup_llm(env: dict[str, str]) -> dict[str, str]:
    """Setup LLM provider."""
    console.print(Panel(
        "[bold]Step 1: LLM Provider[/bold]\n\n"
        "DevOps Copilot needs an AI model to investigate incidents.\n"
        "Choose your preferred provider:",
        border_style="cyan"
    ))

    options = SERVICES["llm"]["options"]

    # Show options
    console.print()
    for i, (key, opt) in enumerate(options.items(), 1):
        console.print(f"  [bold cyan]{i}[/bold cyan]. {opt['name']}")
    console.print()

    choice = Prompt.ask(
        "Select provider",
        choices=[str(i) for i in range(1, len(options) + 1)],
        default="1"
    )

    provider_key = list(options.keys())[int(choice) - 1]
    provider = options[provider_key]

    env["COPILOT_PROVIDER"] = provider["provider_value"]

    for var in provider["vars"]:
        current = env.get(var, "")
        masked = f"{current[:8]}..." if len(current) > 10 else current

        console.print()
        if current:
            console.print(f"  [dim]Current: {masked}[/dim]")
            if not Confirm.ask(f"Update {var}?", default=False):
                continue

        value = Prompt.ask(f"  Enter {var}", password=True)
        if value:
            env[var] = value

    console.print("[green]✓ LLM provider configured![/green]")
    return env


def setup_service(env: dict[str, str], service_key: str) -> dict[str, str]:
    """Setup a single service."""
    svc = SERVICES[service_key]

    console.print(Panel(
        f"[bold]{svc['name']}[/bold]\n\n"
        f"{svc['description']}\n\n"
        "[dim]Leave blank to skip and use demo mode.[/dim]",
        border_style="cyan"
    ))

    # Required vars
    for var in svc.get("vars", []):
        current = env.get(var, "")
        hint = svc.get("hints", {}).get(var, "")
        default = svc.get("defaults", {}).get(var, "")

        prompt = f"  {var}"
        if hint:
            prompt += f" [dim]({hint})[/dim]"

        is_secret = "KEY" in var or "TOKEN" in var or "SECRET" in var
        value = Prompt.ask(prompt, default=current or default, password=is_secret)

        if value:
            env[var] = value

    # Optional vars
    for var in svc.get("optional_vars", []):
        current = env.get(var, "")
        default = svc.get("defaults", {}).get(var, "")
        hint = svc.get("hints", {}).get(var, "")

        prompt = f"  {var} (optional)"
        if hint:
            prompt += f" [dim]({hint})[/dim]"

        value = Prompt.ask(prompt, default=current or default)
        if value:
            env[var] = value

    return env


async def test_connection(service: str) -> bool:
    """Test if a service connection works."""
    # Placeholder for actual connection tests
    await asyncio.sleep(0.5)
    return True


def run_wizard() -> None:
    """Run the interactive setup wizard."""
    console.print(LOGO)

    console.print(Panel(
        "[bold white]Welcome to DevOps Copilot Setup![/bold white]\n\n"
        "This wizard will help you connect your infrastructure services.\n"
        "You can skip any service to use demo mode instead.\n\n"
        "[dim]Press Ctrl+C at any time to exit.[/dim]",
        border_style="green"
    ))
    console.print()

    if not Confirm.ask("Ready to begin?", default=True):
        console.print("[yellow]Setup cancelled.[/yellow]")
        return

    env = load_env()

    try:
        # Step 1: LLM (required)
        console.print()
        env = setup_llm(env)
        save_env(env)

        # Step 2: Optional services
        optional_services = ["datadog", "pagerduty", "kubernetes", "github", "sentry", "slack"]

        console.print()
        console.print(Panel(
            "[bold]Step 2: Connect Services (Optional)[/bold]\n\n"
            "Connect your observability and infrastructure services.\n"
            "Skip any to use built-in demo fixtures.",
            border_style="cyan"
        ))

        for svc_key in optional_services:
            svc = SERVICES[svc_key]
            console.print()

            if Confirm.ask(f"Configure [bold]{svc['name']}[/bold]?", default=False):
                env = setup_service(env, svc_key)
                save_env(env)
                console.print(f"[green]✓ {svc['name']} configured![/green]")
            else:
                console.print(f"[dim]Skipped {svc['name']} (using demo mode)[/dim]")

        # Done!
        console.print()
        console.print(Panel(
            "[bold green]✓ Setup Complete![/bold green]\n\n"
            "Your configuration has been saved to [bold].env[/bold]\n\n"
            "[bold]Next steps:[/bold]\n"
            "  1. Start the backend:  [cyan]uv run uvicorn app.api.main:app --reload[/cyan]\n"
            "  2. Start the frontend: [cyan]cd frontend && npm run dev[/cyan]\n"
            "  3. Or use the CLI:     [cyan]uv run copilot \"Why are checkouts failing?\"[/cyan]\n\n"
            "[dim]Run [bold]copilot-setup --check[/bold] anytime to see connection status.[/dim]",
            border_style="green"
        ))

    except KeyboardInterrupt:
        console.print("\n[yellow]Setup interrupted. Progress has been saved.[/yellow]")
        save_env(env)


def reset_config() -> None:
    """Reset configuration to demo mode."""
    if not Confirm.ask("[yellow]Reset all connections to demo mode?[/yellow]", default=False):
        console.print("Cancelled.")
        return

    # Keep only the LLM key
    env = load_env()
    keep_keys = {"COPILOT_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                 "GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"}

    new_env = {k: v for k, v in env.items() if k in keep_keys}
    save_env(new_env)

    console.print("[green]✓ Configuration reset to demo mode.[/green]")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="DevOps Copilot Setup Wizard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  copilot-setup              Interactive setup wizard
  copilot-setup --check      Check connection status
  copilot-setup --reset      Reset to demo mode
        """
    )
    parser.add_argument("--check", "-c", action="store_true", help="Check connection status")
    parser.add_argument("--reset", "-r", action="store_true", help="Reset to demo mode")

    args = parser.parse_args()

    if args.check:
        show_status()
    elif args.reset:
        reset_config()
    else:
        run_wizard()


if __name__ == "__main__":
    main()
