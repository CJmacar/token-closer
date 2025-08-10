# 🚀 Quick Start Guide

## Get Running in 3 Steps

### 1. Install Solana CLI Tools
```bash
./install_solana.sh
```
This script will install everything you need automatically.

### 2. Configure Your Wallet
```bash
# Create a new keypair (if you don't have one)
solana-keygen new

# Set as default
solana config set --keypair ~/.config/solana/id.json

# Set RPC endpoint (choose one)
solana config set --url https://api.mainnet-beta.solana.com  # Mainnet
solana config set --url https://api.devnet.solana.com        # Devnet
```

### 3. Launch the Application
```bash
./launch.sh
```

## 🧪 Try the Demo First

Want to see how it works without setting up Solana?

```bash
python3 demo.py
```

This runs a demo version with sample data - perfect for testing the interface!

## 📁 What You Get

- **`token_closer.py`** - Main application
- **`demo.py`** - Demo version for testing
- **`launch.sh`** - Easy launcher script
- **`install_solana.sh`** - Automatic Solana CLI installer
- **`README.md`** - Full documentation

## ⚠️ Important Notes

- Make sure you have enough SOL for transaction fees
- Test on devnet first with small amounts
- Keep your keypair file secure
- The app optimizes costs by batching operations

## 🆘 Need Help?

- Check the operation log in the app
- Run `spl-token --help` to verify CLI tools
- See `README.md` for detailed troubleshooting
- Ensure your RPC endpoint is accessible

---

**Happy token account management! 🎉** 