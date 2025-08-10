#!/bin/bash

# Solana Token Account Closer Launcher
# This script launches the Python GUI application

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
if [ ! -f "token_closer.py" ]; then
    echo "❌ Error: token_closer.py not found in current directory"
    echo "Please run this script from the project directory"
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
echo "✅ spl-token found: $(spl-token --version)"
echo "✅ Starting application..."

# Launch the application
python3 token_closer.py

echo "👋 Application closed" 