# Solana Token Account Closer

A simple graphical user interface application for closing Solana token accounts on macOS. This application helps you manage and close multiple Solana token accounts efficiently, minimizing transaction costs through batch operations.

## Features

- 🔍 **List all token accounts** using `spl-token accounts`
- ☑️ **Multiple selection** of accounts to close
- 🗑️ **Batch closing** of selected accounts using `spl-token close`
- 💰 **Cost optimization** by grouping multiple close operations
- 📊 **Real-time status** and operation logging
- 🎨 **Clean, intuitive interface** built with tkinter

## Prerequisites

Before running this application, you need:

1. **Python 3.7+** (usually pre-installed on macOS)
2. **Solana CLI tools** with `spl-token` command
3. **A configured Solana wallet** (keypair file or connected wallet)

### Installing Solana CLI Tools

```bash
# Install Solana CLI tools
sh -c "$(curl -sSfL https://release.solana.com/stable/install)"

# Add to your PATH (add this to ~/.zshrc or ~/.bash_profile)
export PATH="$HOME/.local/share/solana/install/active_release/bin:$PATH"

# Install SPL Token CLI
cargo install spl-token-cli

# Verify installation
solana --version
spl-token --version
```

### Setting up your Solana wallet

```bash
# Create a new keypair (if you don't have one)
solana-keygen new

# Set your keypair as default
solana config set --keypair ~/.config/solana/id.json

# Set your RPC endpoint (choose one)
solana config set --url https://api.mainnet-beta.solana.com  # Mainnet
solana config set --url https://api.devnet.solana.com        # Devnet
solana config set --url https://api.testnet.solana.com       # Testnet

# Check your configuration
solana config get
```

## Installation

1. **Clone or download** this repository to your local machine
2. **Navigate** to the project directory:
   ```bash
   cd "token closer"
   ```
3. **Run the application**:
   ```bash
   python3 token_closer.py
   ```

## Usage

### 1. Launch the Application
Run `python3 token_closer.py` from the project directory.

### 2. Refresh Token Accounts
Click the "🔄 Refresh Accounts" button to load all your current token accounts.

### 3. Select Accounts to Close
- **Single selection**: Double-click on any account row to toggle selection
- **Select all**: Click "☑️ Select All" to select all visible accounts
- **Deselect all**: Click "☐ Deselect All" to clear all selections

### 4. Close Selected Accounts
Click "🗑️ Close Selected" to begin the batch closure process.

### 5. Monitor Progress
Watch the operation log at the bottom of the window for real-time status updates.

## How It Works

### Cost Optimization
The application is designed to minimize transaction costs by:
- Allowing you to select multiple accounts before closing
- Providing clear feedback on the number of accounts selected
- Using efficient `spl-token close` commands for each account

### Safety Features
- **Confirmation dialog** before closing any accounts
- **Real-time logging** of all operations
- **Error handling** for failed operations
- **Account refresh** after closure operations

### Technical Details
- Uses `spl-token accounts --output json` to get account information
- Parses JSON output to display account details
- Runs `spl-token close <address>` for each selected account
- All operations run in background threads to keep the UI responsive

## Troubleshooting

### Common Issues

1. **"spl-token CLI tool not found"**
   - Ensure Solana CLI tools are installed and in your PATH
   - Run `spl-token --version` in terminal to verify

2. **"Failed to get accounts"**
   - Check your Solana configuration: `solana config get`
   - Verify your RPC endpoint is accessible
   - Ensure you have sufficient SOL for transaction fees

3. **"Failed to close account"**
   - Check if you have enough SOL for transaction fees
   - Verify the account still exists and is owned by your wallet
   - Check the operation log for specific error messages

4. **Application appears frozen**
   - Operations run in background threads
   - Check the operation log for progress updates
   - Wait for operations to complete

### Getting Help

- Check the operation log for detailed error messages
- Verify your Solana CLI tools are up to date
- Ensure you have sufficient SOL for transaction fees
- Check your internet connection and RPC endpoint accessibility

## Security Notes

⚠️ **Important Security Considerations:**

- This application runs `spl-token` commands on your system
- Ensure you're running the application from a trusted source
- Verify your Solana configuration before use
- Keep your keypair file secure and never share it
- Test with small amounts on devnet first

## Development

This application is built with:
- **Python 3.7+** (standard library only)
- **tkinter** for the graphical interface
- **subprocess** for running CLI commands
- **threading** for non-blocking operations

### File Structure
```
token closer/
├── token_closer.py    # Main application
├── requirements.txt   # Dependencies (none external)
└── README.md         # This file
```

## License

This project is open source. Feel free to modify and distribute as needed.

## Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests.

---

**Happy token account management! 🚀** 