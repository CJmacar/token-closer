#!/bin/bash

# Solana CLI Tools Installation Script for macOS
# This script helps install Solana CLI tools and SPL Token CLI

echo "🔧 Installing Solana CLI Tools for macOS..."

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew not found. Installing Homebrew first..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add Homebrew to PATH for this session
    if [[ $(uname -m) == 'arm64' ]]; then
        export PATH="/opt/homebrew/bin:$PATH"
    else
        export PATH="/usr/local/bin:$PATH"
    fi
fi

echo "✅ Homebrew found/installed"

# Install Solana CLI tools
echo "📦 Installing Solana CLI tools..."
sh -c "$(curl -sSfL https://release.solana.com/stable/install)"

# Add Solana to PATH
SOLANA_HOME="$HOME/.local/share/solana/install/active_release/bin"
export PATH="$SOLANA_HOME:$PATH"

# Add to shell profile
SHELL_PROFILE=""
if [[ "$SHELL" == *"zsh"* ]]; then
    SHELL_PROFILE="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
    SHELL_PROFILE="$HOME/.bash_profile"
else
    SHELL_PROFILE="$HOME/.profile"
fi

# Check if Solana is already in PATH
if ! grep -q "solana/install/active_release/bin" "$SHELL_PROFILE" 2>/dev/null; then
    echo "export PATH=\"$SOLANA_HOME:\$PATH\"" >> "$SHELL_PROFILE"
    echo "✅ Added Solana to $SHELL_PROFILE"
else
    echo "✅ Solana already in $SHELL_PROFILE"
fi

# Install SPL Token CLI
echo "🔑 Installing SPL Token CLI..."
cargo install spl-token-cli

# Verify installations
echo "🔍 Verifying installations..."

# Source the profile to get updated PATH
source "$SHELL_PROFILE"

if command -v solana &> /dev/null; then
    echo "✅ Solana CLI installed: $(solana --version)"
else
    echo "❌ Solana CLI installation failed"
fi

if command -v spl-token &> /dev/null; then
    echo "✅ SPL Token CLI installed: $(spl-token --version)"
else
    echo "❌ SPL Token CLI installation failed"
fi

echo ""
echo "🎉 Installation complete!"
echo ""
echo "Next steps:"
echo "1. Restart your terminal or run: source $SHELL_PROFILE"
echo "2. Configure your Solana wallet:"
echo "   - Create keypair: solana-keygen new"
echo "   - Set keypair: solana config set --keypair ~/.config/solana/id.json"
echo "   - Set RPC endpoint: solana config set --url https://api.mainnet-beta.solana.com"
echo "3. Run the token closer: ./launch.sh"
echo ""
echo "For more help, see README.md" 