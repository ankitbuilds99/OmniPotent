#!/bin/bash

# DevOps Copilot - Quick Start Script
# Usage: ./start.sh [setup|check|api|web|all]

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color
BOLD='\033[1m'

print_logo() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║                                                           ║"
    echo "║     ██████╗ ███████╗██╗   ██╗ ██████╗ ██████╗ ███████╗   ║"
    echo "║     ██╔══██╗██╔════╝██║   ██║██╔═══██╗██╔══██╗██╔════╝   ║"
    echo "║     ██║  ██║█████╗  ██║   ██║██║   ██║██████╔╝███████╗   ║"
    echo "║     ██║  ██║██╔══╝  ╚██╗ ██╔╝██║   ██║██╔═══╝ ╚════██║   ║"
    echo "║     ██████╔╝███████╗ ╚████╔╝ ╚██████╔╝██║     ███████║   ║"
    echo "║     ╚═════╝ ╚══════╝  ╚═══╝   ╚═════╝ ╚═╝     ╚══════╝   ║"
    echo "║                                                           ║"
    echo "║                    C O P I L O T                          ║"
    echo "║                                                           ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

check_dependencies() {
    echo -e "${CYAN}Checking dependencies...${NC}"

    # Check Python
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}✗ Python 3 not found. Please install Python 3.11+${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓${NC} Python $(python3 --version | cut -d' ' -f2)"

    # Check uv
    if ! command -v uv &> /dev/null; then
        echo -e "${YELLOW}Installing uv...${NC}"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
    echo -e "${GREEN}✓${NC} uv installed"

    # Check Node (for frontend)
    if command -v node &> /dev/null; then
        echo -e "${GREEN}✓${NC} Node.js $(node --version)"
    else
        echo -e "${YELLOW}⚠${NC} Node.js not found (needed for frontend)"
    fi

    echo ""
}

install_backend() {
    echo -e "${CYAN}Installing Python dependencies...${NC}"
    uv venv 2>/dev/null || true
    uv pip install -e . -q
    echo -e "${GREEN}✓${NC} Backend dependencies installed"
}

setup_env() {
    if [ ! -f .env ]; then
        echo -e "${YELLOW}Creating .env from template...${NC}"
        cp .env.example .env
    fi
}

run_setup_wizard() {
    echo -e "${CYAN}Starting setup wizard...${NC}"
    echo ""
    uv run copilot-setup
}

check_status() {
    uv run copilot-setup --check
}

start_api() {
    echo -e "${CYAN}Starting API server on http://localhost:8000${NC}"
    uv run uvicorn app.api.main:app --reload --port 8000
}

start_frontend() {
    echo -e "${CYAN}Starting frontend on http://localhost:5173${NC}"
    cd frontend
    npm install -q 2>/dev/null
    npm run dev
}

start_all() {
    echo -e "${CYAN}Starting DevOps Copilot...${NC}"
    echo ""

    # Start API in background
    echo -e "${GREEN}→${NC} Starting API server..."
    uv run uvicorn app.api.main:app --reload --port 8000 &
    API_PID=$!
    sleep 3

    # Start frontend in background
    echo -e "${GREEN}→${NC} Starting frontend..."
    cd frontend
    npm install -q 2>/dev/null
    npm run dev &
    FRONTEND_PID=$!
    cd ..

    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ DevOps Copilot is running!${NC}"
    echo ""
    echo -e "  ${BOLD}Web Console:${NC}  http://localhost:5173"
    echo -e "  ${BOLD}API Server:${NC}   http://localhost:8000"
    echo -e "  ${BOLD}API Docs:${NC}     http://localhost:8000/docs"
    echo ""
    echo -e "  Press ${BOLD}Ctrl+C${NC} to stop all services"
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"

    # Wait for Ctrl+C
    trap "kill $API_PID $FRONTEND_PID 2>/dev/null; exit" INT
    wait
}

show_help() {
    print_logo
    echo -e "${BOLD}Usage:${NC} ./start.sh [command]"
    echo ""
    echo -e "${BOLD}Commands:${NC}"
    echo -e "  ${CYAN}setup${NC}     Run interactive setup wizard"
    echo -e "  ${CYAN}check${NC}     Check connection status"
    echo -e "  ${CYAN}api${NC}       Start API server only"
    echo -e "  ${CYAN}web${NC}       Start frontend only"
    echo -e "  ${CYAN}all${NC}       Start both API and frontend"
    echo -e "  ${CYAN}cli${NC}       Start CLI interface"
    echo -e "  ${CYAN}help${NC}      Show this help message"
    echo ""
    echo -e "${BOLD}Quick Start:${NC}"
    echo -e "  1. Run ${CYAN}./start.sh setup${NC} to configure your services"
    echo -e "  2. Run ${CYAN}./start.sh all${NC} to start the application"
    echo -e "  3. Open ${CYAN}http://localhost:5173${NC} in your browser"
    echo ""
    echo -e "${BOLD}CLI Usage:${NC}"
    echo -e "  ${CYAN}uv run copilot \"Why is checkout failing?\"${NC}"
    echo ""
}

run_cli() {
    echo -e "${CYAN}Starting CLI interface...${NC}"
    echo ""
    uv run copilot
}

# Main
case "${1:-help}" in
    setup)
        print_logo
        check_dependencies
        install_backend
        setup_env
        run_setup_wizard
        ;;
    check)
        install_backend
        check_status
        ;;
    api)
        print_logo
        install_backend
        setup_env
        start_api
        ;;
    web)
        print_logo
        start_frontend
        ;;
    all)
        print_logo
        check_dependencies
        install_backend
        setup_env
        start_all
        ;;
    cli)
        print_logo
        install_backend
        setup_env
        run_cli
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        show_help
        exit 1
        ;;
esac
