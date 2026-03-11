#!/bin/bash

# Solana Token Account Closer Launcher
# This script launches the application in either GUI or Web mode

show_help() {
    echo "Solana Token Account Closer"
    echo ""
    echo "Usage: ./launch.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --web, -w       Launch web interface only (opens in browser)"
    echo "  --both, -b      Launch both desktop GUI and web interface"
    echo "  --port, -p      Port for web interface (default: 8080)"
    echo "  --no-browser    Do not automatically open browser"
    echo "  --help, -h      Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./launch.sh              # Launch desktop GUI only"
    echo "  ./launch.sh --web        # Launch web interface only"
    echo "  ./launch.sh --both       # Launch both GUI and web"
    echo "  ./launch.sh -b -p 3000   # Both interfaces, web on port 3000"
}

# Parse arguments
MODE_ARG=""
PORT_ARG=""
NO_BROWSER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --web|-w)
            MODE_ARG="--web"
            shift
            ;;
        --both|-b)
            MODE_ARG="--both"
            shift
            ;;
        --port|-p)
            PORT_ARG="--port $2"
            shift 2
            ;;
        --no-browser)
            NO_BROWSER="--no-browser"
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

echo "🚀 Launching Solana Token Account Closer..."

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed or not in PATH"
    echo "Please install Python 3 and try again"
    exit 1
fi

# Check if spl-token is available
if ! command -v spl-token &> /dev/null; then
    echo "❌ Error: spl-token CLI tool is not installed or not in PATH"
    echo "Please install Solana CLI tools and ensure spl-token is available"
    echo "Visit: https://docs.solana.com/cli/install-solana-cli-tools"
    exit 1
fi

# Check if the main Python file exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$SCRIPT_DIR/token_closer.py" ]; then
    echo "❌ Error: token_closer.py not found in script directory"
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
echo "✅ spl-token found: $(spl-token --version)"

if [ "$MODE_ARG" = "--both" ]; then
    echo "✅ Starting desktop GUI and web interface..."
elif [ "$MODE_ARG" = "--web" ]; then
    echo "✅ Starting web interface..."
else
    echo "✅ Starting desktop GUI..."
fi

# Launch the application
python3 "$SCRIPT_DIR/token_closer.py" $MODE_ARG $PORT_ARG $NO_BROWSER

echo "👋 Application closed" 