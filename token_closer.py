#!/usr/bin/env python3
"""
Solana Token Account Closer
A GUI application to close Solana token accounts and recover rent.

Security: Input validation, shell sanitization, secure temp files
Architecture: Separated concerns with dataclasses and type hints
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# Tkinter is optional - only needed for GUI mode
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


# =============================================================================
# Constants & Configuration
# =============================================================================

class Config:
    """Application configuration constants."""
    APP_NAME = "Solana Token Account Closer"
    APP_VERSION = "1.1.0"
    WINDOW_SIZE = "1150x750"
    
    # Solana address validation: Base58, 32-44 characters
    SOLANA_ADDRESS_PATTERN = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
    
    # API endpoints (tried in order)
    TOKEN_LIST_URLS = [
        "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json",
        "https://cdn.jsdelivr.net/gh/solana-labs/token-list@main/src/tokens/solana.tokenlist.json",
        "https://token.jup.ag/all",
    ]
    
    # Timeouts (seconds)
    API_TIMEOUT = 15
    CLI_TIMEOUT = 30
    METADATA_FETCH_TIMEOUT = 10
    
    # Rent estimate for token accounts (SOL)
    TOKEN_ACCOUNT_RENT = 0.00203928
    TX_FEE = 0.000005
    
    # Rate limiting
    API_RATE_LIMIT_DELAY = 0.1  # seconds between API calls


class LogLevel(Enum):
    """Log message severity levels."""
    INFO = ("black", "INFO")
    SUCCESS = ("green", "SUCCESS")
    WARNING = ("orange", "WARNING")
    ERROR = ("red", "ERROR")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class TokenMetadata:
    """Token metadata information."""
    name: str = ""
    symbol: str = ""
    description: str = ""
    
    @property
    def display_name(self) -> str:
        return self.name or "—"
    
    @property
    def display_symbol(self) -> str:
        return self.symbol or "—"


@dataclass
class TokenAccount:
    """Represents a Solana token account."""
    address: str
    mint: str
    balance: str
    decimals: int
    owner: str
    is_native: bool = False
    
    @classmethod
    def from_json(cls, data: dict) -> TokenAccount:
        """Create TokenAccount from JSON data."""
        token_amount = data.get('tokenAmount', {})
        return cls(
            address=data.get('address', ''),
            mint=data.get('mint', ''),
            balance=token_amount.get('uiAmountString', '0'),
            decimals=token_amount.get('decimals', 0),
            owner=data.get('owner', ''),
            is_native=data.get('isNative', False),
        )
    
    @property
    def display_address(self) -> str:
        """Truncated address for display."""
        if len(self.address) > 20:
            return f"{self.address[:8]}...{self.address[-6:]}"
        return self.address
    
    @property
    def display_mint(self) -> str:
        """Truncated mint for display."""
        if len(self.mint) > 20:
            return f"{self.mint[:8]}...{self.mint[-6:]}"
        return self.mint


@dataclass
class OperationResult:
    """Result of a command operation."""
    success: bool
    output: str = ""
    error: str = ""


# =============================================================================
# Security Utilities
# =============================================================================

class SecurityUtils:
    """Security-related utility functions."""
    
    @staticmethod
    def is_valid_solana_address(address: str) -> bool:
        """Validate Solana address format."""
        if not address or not isinstance(address, str):
            return False
        return bool(Config.SOLANA_ADDRESS_PATTERN.match(address))
    
    @staticmethod
    def sanitize_for_shell(value: str) -> str:
        """Safely escape value for shell commands."""
        return shlex.quote(value)
    
    @staticmethod
    def validate_addresses(addresses: List[str]) -> Tuple[List[str], List[str]]:
        """Validate a list of addresses, return (valid, invalid)."""
        valid = []
        invalid = []
        for addr in addresses:
            if SecurityUtils.is_valid_solana_address(addr):
                valid.append(addr)
            else:
                invalid.append(addr)
        return valid, invalid


# =============================================================================
# Command Executor
# =============================================================================

class CommandExecutor:
    """Executes shell commands safely."""
    
    @staticmethod
    def run(command: List[str], timeout: int = Config.CLI_TIMEOUT) -> OperationResult:
        """Run a shell command and return the result."""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return OperationResult(
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr
            )
        except subprocess.TimeoutExpired:
            return OperationResult(False, "", f"Command timed out after {timeout}s")
        except FileNotFoundError:
            return OperationResult(False, "", "Command not found")
        except Exception as e:
            return OperationResult(False, "", str(e))
    
    @staticmethod
    def check_spl_token_available() -> bool:
        """Check if spl-token CLI is available."""
        result = CommandExecutor.run(['spl-token', '--version'], timeout=5)
        return result.success


# =============================================================================
# Metadata Service
# =============================================================================

class MetadataService:
    """Fetches and caches token metadata from various sources."""
    
    def __init__(self):
        self._cache: Dict[str, TokenMetadata] = {}
        self._lock = threading.Lock()
        self._loading = False
    
    def get(self, mint: str) -> TokenMetadata:
        """Get metadata for a mint address."""
        with self._lock:
            return self._cache.get(mint, TokenMetadata())
    
    def set(self, mint: str, metadata: TokenMetadata) -> None:
        """Cache metadata for a mint address."""
        with self._lock:
            self._cache[mint] = metadata
    
    def has(self, mint: str) -> bool:
        """Check if metadata is cached for a mint."""
        with self._lock:
            return mint in self._cache
    
    @property
    def cache_size(self) -> int:
        """Number of cached entries."""
        with self._lock:
            return len(self._cache)
    
    def load_from_api(self, callback: Callable[[bool, str], None]) -> None:
        """Load metadata from token list APIs in background."""
        if self._loading:
            return
        
        self._loading = True
        
        def fetch():
            last_error = ""
            
            for url in Config.TOKEN_LIST_URLS:
                try:
                    req = urllib.request.Request(
                        url,
                        headers={'User-Agent': f'TokenCloser/{Config.APP_VERSION}'}
                    )
                    with urllib.request.urlopen(req, timeout=Config.API_TIMEOUT) as response:
                        data = json.loads(response.read().decode('utf-8'))
                        
                        # Handle different response formats
                        tokens = data
                        if isinstance(data, dict):
                            tokens = data.get('tokens', data.get('data', []))
                        
                        if not isinstance(tokens, list):
                            continue
                        
                        count = 0
                        with self._lock:
                            for token in tokens:
                                mint = token.get('address', '')
                                if mint:
                                    self._cache[mint] = TokenMetadata(
                                        name=token.get('name', ''),
                                        symbol=token.get('symbol', ''),
                                        description=token.get('description', '') or token.get('name', '')
                                    )
                                    count += 1
                        
                        if count > 0:
                            self._loading = False
                            callback(True, f"Loaded metadata for {count} tokens")
                            return
                            
                except urllib.error.URLError as ex:
                    last_error = str(getattr(ex, 'reason', ex))
                except Exception as ex:
                    last_error = str(ex)
            
            self._loading = False
            callback(False, f"Could not load token metadata: {last_error}")
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def fetch_from_cli(self, mint: str) -> Optional[TokenMetadata]:
        """Fetch metadata using spl-token display command."""
        if not SecurityUtils.is_valid_solana_address(mint):
            return None
        
        result = CommandExecutor.run(
            ['spl-token', 'display', mint],
            timeout=Config.METADATA_FETCH_TIMEOUT
        )
        
        if result.success and result.output:
            name = symbol = ""
            for line in result.output.split('\n'):
                if 'Name:' in line:
                    name = line.split('Name:')[-1].strip()
                elif 'Symbol:' in line:
                    symbol = line.split('Symbol:')[-1].strip()
            
            if name or symbol:
                return TokenMetadata(name=name, symbol=symbol, description=name)
        
        return None
    
    def fetch_from_dexscreener(self, mint: str) -> Optional[TokenMetadata]:
        """Fetch metadata from DexScreener API."""
        if not SecurityUtils.is_valid_solana_address(mint):
            return None
        
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            req = urllib.request.Request(
                url,
                headers={'User-Agent': f'TokenCloser/{Config.APP_VERSION}'}
            )
            with urllib.request.urlopen(req, timeout=Config.METADATA_FETCH_TIMEOUT) as response:
                data = json.loads(response.read().decode('utf-8'))
                pairs = data.get('pairs', [])
                
                if pairs:
                    for token_key in ['baseToken', 'quoteToken']:
                        token = pairs[0].get(token_key, {})
                        if token.get('address', '').lower() == mint.lower():
                            name = token.get('name', '')
                            symbol = token.get('symbol', '')
                            if name or symbol:
                                return TokenMetadata(name=name, symbol=symbol, description=name)
        except Exception:
            pass
        
        return None
    
    def fetch_from_metaplex(self, mint: str) -> Optional[TokenMetadata]:
        """Fetch metadata from on-chain Metaplex metadata account."""
        if not SecurityUtils.is_valid_solana_address(mint):
            return None
        
        try:
            # Derive metadata PDA using solana CLI
            METADATA_PROGRAM = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
            
            result = CommandExecutor.run([
                'solana', 'find-program-derived-address', METADATA_PROGRAM,
                f'string:metadata',
                f'pubkey:{METADATA_PROGRAM}',
                f'pubkey:{mint}'
            ], timeout=5)
            
            if not result.success or not result.output.strip():
                return None
            
            pda = result.output.strip().split()[0]
            
            # Fetch the metadata account
            account_result = CommandExecutor.run(
                ['solana', 'account', pda, '--output', 'json'],
                timeout=Config.METADATA_FETCH_TIMEOUT
            )
            
            if not account_result.success:
                return None
            
            account_data = json.loads(account_result.output)
            data_b64 = account_data.get('account', {}).get('data', [''])[0]
            
            if not data_b64:
                return None
            
            raw = base64.b64decode(data_b64)
            
            # Parse Metaplex metadata structure
            # Offset 65: name length (4 bytes) + name string
            if len(raw) < 100:
                return None
            
            pos = 65  # After key (1) + update_auth (32) + mint (32)
            
            # Read name
            name_len = int.from_bytes(raw[pos:pos+4], 'little')
            pos += 4
            name = raw[pos:pos+min(name_len, 100)].decode('utf-8', errors='ignore').rstrip('\x00').strip()
            pos += name_len
            
            # Read symbol
            if pos + 4 < len(raw):
                symbol_len = int.from_bytes(raw[pos:pos+4], 'little')
                pos += 4
                symbol = raw[pos:pos+min(symbol_len, 20)].decode('utf-8', errors='ignore').rstrip('\x00').strip()
            else:
                symbol = ""
            
            if name or symbol:
                return TokenMetadata(name=name, symbol=symbol, description=name)
                
        except Exception:
            pass
        
        return None
    
    def fetch_missing(self, mints: Set[str], progress_callback: Callable[[int, int], None]) -> int:
        """Fetch metadata for mints not in cache. Returns count fetched."""
        to_fetch = [m for m in mints if not self.has(m) and SecurityUtils.is_valid_solana_address(m)]
        
        if not to_fetch:
            return 0
        
        fetched = 0
        for i, mint in enumerate(to_fetch):
            # Try sources in order: CLI -> DexScreener -> Metaplex (on-chain)
            metadata = (
                self.fetch_from_cli(mint) or 
                self.fetch_from_dexscreener(mint) or
                self.fetch_from_metaplex(mint)
            )
            
            if metadata:
                self.set(mint, metadata)
                fetched += 1
            
            progress_callback(i + 1, len(to_fetch))
        
        return fetched


# =============================================================================
# Theme & Styles
# =============================================================================

class AppTheme:
    """Application theme and styling."""
    
    # Colors
    BG_PRIMARY = "#f8f9fa"
    BG_SECONDARY = "#ffffff"
    ACCENT = "#0d6efd"
    ACCENT_HOVER = "#0b5ed7"
    DANGER = "#dc3545"
    DANGER_HOVER = "#bb2d3b"
    SUCCESS = "#198754"
    WARNING = "#ffc107"
    TEXT_PRIMARY = "#212529"
    TEXT_SECONDARY = "#6c757d"
    BORDER = "#dee2e6"
    
    @classmethod
    def apply(cls, root: tk.Tk) -> None:
        """Apply theme to the application."""
        style = ttk.Style()
        
        # Use clam theme as base (more customizable)
        style.theme_use('clam')
        
        # Configure general styles
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background=cls.BG_PRIMARY)
        style.configure('TLabel', background=cls.BG_PRIMARY, foreground=cls.TEXT_PRIMARY)
        style.configure('TLabelframe', background=cls.BG_PRIMARY)
        style.configure('TLabelframe.Label', background=cls.BG_PRIMARY, font=('Segoe UI', 10, 'bold'))
        
        # Button styles
        style.configure('TButton', padding=(12, 6), font=('Segoe UI', 9))
        style.map('TButton',
            background=[('active', cls.ACCENT_HOVER), ('!active', cls.ACCENT)],
            foreground=[('active', 'white'), ('!active', 'white')]
        )
        
        # Danger button
        style.configure('Danger.TButton', padding=(12, 6))
        style.map('Danger.TButton',
            background=[('active', cls.DANGER_HOVER), ('!active', cls.DANGER)],
            foreground=[('active', 'white'), ('!active', 'white')]
        )
        
        # Treeview
        style.configure('Treeview',
            background=cls.BG_SECONDARY,
            foreground=cls.TEXT_PRIMARY,
            fieldbackground=cls.BG_SECONDARY,
            rowheight=28
        )
        style.configure('Treeview.Heading',
            font=('Segoe UI', 9, 'bold'),
            padding=(8, 4)
        )
        style.map('Treeview',
            background=[('selected', cls.ACCENT)],
            foreground=[('selected', 'white')]
        )
        
        # Checkbutton
        style.configure('TCheckbutton', background=cls.BG_PRIMARY)


# =============================================================================
# Main Application
# =============================================================================

class TokenAccountCloser:
    """Main application class."""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{Config.APP_NAME} v{Config.APP_VERSION}")
        self.root.geometry(Config.WINDOW_SIZE)
        self.root.configure(bg=AppTheme.BG_PRIMARY)
        self.root.minsize(900, 600)
        
        # Apply theme
        AppTheme.apply(root)
        
        # State
        self._lock = threading.Lock()
        self.accounts: List[TokenAccount] = []
        self.selected_addresses: Set[str] = set()
        self.metadata_service = MetadataService()
        
        # Build UI
        self._create_ui()
        
        # Initial data load
        self._load_metadata()
        self._refresh_accounts()
    
    # -------------------------------------------------------------------------
    # UI Construction
    # -------------------------------------------------------------------------
    
    def _create_ui(self) -> None:
        """Create the main UI."""
        # Main container
        main = ttk.Frame(self.root, padding=15)
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=3)
        main.rowconfigure(3, weight=1)
        
        # Header
        self._create_header(main)
        
        # Toolbar
        self._create_toolbar(main)
        
        # Account list
        self._create_account_list(main)
        
        # Log panel
        self._create_log_panel(main)
        
        # Status bar
        self._create_status_bar(main)
    
    def _create_header(self, parent: ttk.Frame) -> None:
        """Create header section."""
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        
        title = ttk.Label(
            header,
            text=f"🔐 {Config.APP_NAME}",
            font=('Segoe UI', 18, 'bold')
        )
        title.pack(side=tk.LEFT)
        
        version = ttk.Label(
            header,
            text=f"v{Config.APP_VERSION}",
            foreground=AppTheme.TEXT_SECONDARY
        )
        version.pack(side=tk.LEFT, padx=(10, 0))
    
    def _create_toolbar(self, parent: ttk.Frame) -> None:
        """Create toolbar with action buttons."""
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        
        # Left side buttons
        left = ttk.Frame(toolbar)
        left.pack(side=tk.LEFT)
        
        self.refresh_btn = ttk.Button(left, text="⟳ Refresh", command=self._refresh_accounts)
        self.refresh_btn.pack(side=tk.LEFT, padx=(0, 8))
        
        ttk.Button(left, text="☑ Select All", command=self._select_all).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(left, text="☐ Clear", command=self._deselect_all).pack(side=tk.LEFT, padx=(0, 8))
        
        ttk.Separator(left, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)
        
        ttk.Button(left, text="📋 Preview", command=self._show_preview).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(left, text="🧪 Dry Run", command=self._show_dry_run).pack(side=tk.LEFT, padx=(0, 8))
        
        # Right side - action buttons
        right = ttk.Frame(toolbar)
        right.pack(side=tk.RIGHT)
        
        self.burn_var = tk.BooleanVar()
        burn_cb = ttk.Checkbutton(
            right,
            text="🔥 Burn tokens first",
            variable=self.burn_var,
            command=self._on_burn_changed
        )
        burn_cb.pack(side=tk.LEFT, padx=(0, 15))
        
        self.close_btn = ttk.Button(
            right,
            text="🗑️ Close Selected",
            style='Danger.TButton',
            command=self._close_selected
        )
        self.close_btn.pack(side=tk.LEFT)
    
    def _create_account_list(self, parent: ttk.Frame) -> None:
        """Create the account list treeview."""
        frame = ttk.LabelFrame(parent, text="Token Accounts", padding=8)
        frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        
        # Columns configuration (no decimals column)
        columns = ('select', 'symbol', 'name', 'balance', 'address', 'mint')
        self.tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse')
        
        # Column headings and widths
        col_config = [
            ('select', '✓', 45, 'center'),
            ('symbol', 'Ticker', 100, 'w'),
            ('name', 'Token Name', 220, 'w'),
            ('balance', 'Balance', 140, 'e'),
            ('address', 'Account', 170, 'w'),
            ('mint', 'Mint', 170, 'w'),
        ]
        
        for col_id, heading, width, anchor in col_config:
            self.tree.heading(col_id, text=heading, command=lambda c=col_id: self._sort_column(c))
            self.tree.column(col_id, width=width, anchor=anchor, minwidth=40)
        
        # Scrollbars
        v_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        
        # Layout
        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        
        # Bindings
        self.tree.bind('<Double-1>', self._on_row_double_click)
        self.tree.bind('<Return>', self._on_row_double_click)
        self.tree.bind('<space>', self._on_row_double_click)
        
        # Right-click context menu
        self.tree.bind('<Button-3>', self._on_right_click)  # Windows/Linux
        self.tree.bind('<Button-2>', self._on_right_click)  # macOS
        self._create_context_menu()
    
    def _create_log_panel(self, parent: ttk.Frame) -> None:
        """Create the log panel."""
        frame = ttk.LabelFrame(parent, text="Activity Log", padding=8)
        frame.grid(row=3, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        
        self.log_text = scrolledtext.ScrolledText(
            frame,
            height=6,
            wrap=tk.WORD,
            font=('Consolas', 9),
            bg=AppTheme.BG_SECONDARY,
            relief=tk.FLAT,
            borderwidth=1
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        
        # Configure log colors
        self.log_text.tag_configure('INFO', foreground='black')
        self.log_text.tag_configure('SUCCESS', foreground=AppTheme.SUCCESS)
        self.log_text.tag_configure('WARNING', foreground='#b86e00')
        self.log_text.tag_configure('ERROR', foreground=AppTheme.DANGER)
    
    def _create_status_bar(self, parent: ttk.Frame) -> None:
        """Create status bar."""
        status = ttk.Frame(parent)
        status.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        
        self.status_label = ttk.Label(status, text="Ready", foreground=AppTheme.TEXT_SECONDARY)
        self.status_label.pack(side=tk.LEFT)
        
        self.selection_label = ttk.Label(status, text="", foreground=AppTheme.TEXT_SECONDARY)
        self.selection_label.pack(side=tk.RIGHT)
    
    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    
    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Add a message to the log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = f"[{timestamp}] "
        
        self.log_text.insert(tk.END, prefix, level.value[1])
        self.log_text.insert(tk.END, f"{message}\n", level.value[1])
        self.log_text.see(tk.END)
        
        # Update status
        self.status_label.config(text=message, foreground=level.value[0])
    
    def _log_threadsafe(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Thread-safe logging."""
        self.root.after(0, lambda: self._log(message, level))
    
    # -------------------------------------------------------------------------
    # Data Operations
    # -------------------------------------------------------------------------
    
    def _load_metadata(self) -> None:
        """Load token metadata from APIs."""
        self._log("Loading token metadata...")
        
        def callback(success: bool, message: str):
            level = LogLevel.SUCCESS if success else LogLevel.WARNING
            self._log_threadsafe(message, level)
            if success:
                self.root.after(0, self._update_display)
        
        self.metadata_service.load_from_api(callback)
    
    def _refresh_accounts(self) -> None:
        """Refresh the list of token accounts."""
        self._log("Refreshing token accounts...")
        self.refresh_btn.config(state='disabled')
        
        def fetch():
            result = CommandExecutor.run(['spl-token', 'accounts', '--output', 'json'])
            
            if result.success:
                try:
                    data = json.loads(result.output)
                    accounts = [TokenAccount.from_json(a) for a in data.get('accounts', [])]
                    
                    with self._lock:
                        self.accounts = accounts
                    
                    count = len(accounts)
                    self._log_threadsafe(f"Found {count} token accounts", LogLevel.SUCCESS)
                    self.root.after(0, self._update_display)
                    self.root.after(500, self._fetch_missing_metadata)
                    
                except json.JSONDecodeError:
                    self._log_threadsafe("Failed to parse account data", LogLevel.ERROR)
            else:
                self._log_threadsafe(f"Failed: {result.error}", LogLevel.ERROR)
            
            self.root.after(0, lambda: self.refresh_btn.config(state='normal'))
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _fetch_missing_metadata(self) -> None:
        """Fetch metadata for accounts not in cache."""
        mints = {a.mint for a in self.accounts}
        missing = [m for m in mints if not self.metadata_service.has(m)]
        
        if not missing:
            return
        
        self._log(f"Fetching metadata for {len(missing)} unknown tokens...")
        
        def fetch():
            def progress(current: int, total: int):
                if current % 5 == 0:
                    self.root.after(0, self._update_display)
            
            count = self.metadata_service.fetch_missing(set(missing), progress)
            
            if count > 0:
                self._log_threadsafe(f"Fetched metadata for {count} tokens", LogLevel.SUCCESS)
            self.root.after(0, self._update_display)
        
        threading.Thread(target=fetch, daemon=True).start()
    
    # -------------------------------------------------------------------------
    # UI Updates
    # -------------------------------------------------------------------------
    
    def _update_display(self) -> None:
        """Update the accounts display."""
        # Clear tree
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Populate tree
        with self._lock:
            for account in self.accounts:
                selected = "☑" if account.address in self.selected_addresses else "☐"
                metadata = self.metadata_service.get(account.mint)
                
                item = self.tree.insert('', 'end', values=(
                    selected,
                    metadata.display_symbol,
                    metadata.display_name,
                    account.balance,
                    account.display_address,
                    account.display_mint,
                    account.decimals
                ), tags=(account.address,))
        
        self._update_selection_count()
    
    def _update_selection_count(self) -> None:
        """Update selection count display."""
        count = len(self.selected_addresses)
        
        # Update label
        if count > 0:
            self.selection_label.config(text=f"{count} selected")
        else:
            self.selection_label.config(text="")
        
        # Update button text
        if count > 0:
            action = "🔥 Burn & Close" if self.burn_var.get() else "🗑️ Close"
            self.close_btn.config(text=f"{action} {count}")
        else:
            self.close_btn.config(text="🗑️ Close Selected")
    
    def _sort_column(self, col: str) -> None:
        """Sort treeview by column."""
        items = [(self.tree.set(item, col), item) for item in self.tree.get_children('')]
        items.sort()
        
        for index, (val, item) in enumerate(items):
            self.tree.move(item, '', index)
    
    # -------------------------------------------------------------------------
    # Context Menu (Right-Click)
    # -------------------------------------------------------------------------
    
    def _create_context_menu(self) -> None:
        """Create the right-click context menu."""
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="📋 Copy Ticker", command=lambda: self._copy_column('symbol'))
        self.context_menu.add_command(label="📋 Copy Token Name", command=lambda: self._copy_column('name'))
        self.context_menu.add_command(label="📋 Copy Balance", command=lambda: self._copy_column('balance'))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📋 Copy Account Address (Full)", command=self._copy_full_address)
        self.context_menu.add_command(label="📋 Copy Mint Address (Full)", command=self._copy_full_mint)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="☑ Toggle Selection", command=self._toggle_selected_row)
    
    def _on_right_click(self, event) -> None:
        """Handle right-click to show context menu."""
        # Select the row under cursor
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.tree.focus(item)
            # Show context menu
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()
    
    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to system clipboard."""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # Required for clipboard to persist
        self._log(f"Copied: {text[:50]}{'...' if len(text) > 50 else ''}")
    
    def _copy_column(self, col: str) -> None:
        """Copy the value of a specific column from selected row."""
        selection = self.tree.selection()
        if not selection:
            return
        
        value = self.tree.set(selection[0], col)
        if value and value != "—":
            self._copy_to_clipboard(value)
    
    def _copy_full_address(self) -> None:
        """Copy the full (non-truncated) account address."""
        selection = self.tree.selection()
        if not selection:
            return
        
        tags = self.tree.item(selection[0], 'tags')
        if tags:
            self._copy_to_clipboard(tags[0])
    
    def _copy_full_mint(self) -> None:
        """Copy the full (non-truncated) mint address."""
        selection = self.tree.selection()
        if not selection:
            return
        
        tags = self.tree.item(selection[0], 'tags')
        if tags:
            address = tags[0]
            account = self._get_account_by_address(address)
            if account:
                self._copy_to_clipboard(account.mint)
    
    def _toggle_selected_row(self) -> None:
        """Toggle selection of the currently focused row."""
        selection = self.tree.selection()
        if selection:
            # Simulate double-click event
            self._on_row_double_click(None)
    
    # -------------------------------------------------------------------------
    # Selection Handling
    # -------------------------------------------------------------------------
    
    def _on_row_double_click(self, event) -> None:
        """Handle row double-click to toggle selection."""
        selection = self.tree.selection()
        if not selection:
            return
        
        tags = self.tree.item(selection[0], 'tags')
        if not tags:
            return
        
        address = tags[0]
        if not SecurityUtils.is_valid_solana_address(address):
            self._log(f"Invalid address: {address[:20]}...", LogLevel.ERROR)
            return
        
        with self._lock:
            if address in self.selected_addresses:
                self.selected_addresses.discard(address)
            else:
                self.selected_addresses.add(address)
        
        self._update_display()
    
    def _select_all(self) -> None:
        """Select all accounts."""
        with self._lock:
            self.selected_addresses.clear()
            for account in self.accounts:
                if SecurityUtils.is_valid_solana_address(account.address):
                    self.selected_addresses.add(account.address)
        
        self._update_display()
        self._log(f"Selected {len(self.selected_addresses)} accounts")
    
    def _deselect_all(self) -> None:
        """Deselect all accounts."""
        with self._lock:
            self.selected_addresses.clear()
        self._update_display()
        self._log("Cleared selection")
    
    def _on_burn_changed(self) -> None:
        """Handle burn checkbox change."""
        if self.burn_var.get():
            self._log("🔥 Burn before close enabled")
        self._update_selection_count()
    
    # -------------------------------------------------------------------------
    # Account Operations
    # -------------------------------------------------------------------------
    
    def _get_account_by_address(self, address: str) -> Optional[TokenAccount]:
        """Find account by address."""
        for account in self.accounts:
            if account.address == address:
                return account
        return None
    
    def _show_preview(self) -> None:
        """Show command preview dialog."""
        if not self.selected_addresses:
            messagebox.showinfo("No Selection", "Please select at least one account.")
            return
        
        preview = self._generate_preview()
        self._show_text_dialog("Command Preview", preview, "⚠️ Preview only - no changes made")
    
    def _show_dry_run(self) -> None:
        """Show dry run report dialog."""
        if not self.selected_addresses:
            messagebox.showinfo("No Selection", "Please select at least one account.")
            return
        
        report = self._generate_dry_run_report()
        self._show_text_dialog("Dry Run Report", report, "✅ No accounts will be closed")
    
    def _show_text_dialog(self, title: str, content: str, subtitle: str) -> None:
        """Show a text dialog window."""
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("700x500")
        dialog.transient(self.root)
        dialog.grab_set()
        
        ttk.Label(dialog, text=f"📋 {title}", font=('Segoe UI', 14, 'bold')).pack(pady=(20, 5))
        ttk.Label(dialog, text=subtitle, foreground=AppTheme.TEXT_SECONDARY).pack(pady=(0, 15))
        
        text = scrolledtext.ScrolledText(dialog, wrap=tk.WORD, font=('Consolas', 9))
        text.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 15))
        text.insert(tk.END, content)
        text.config(state=tk.DISABLED)
        
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 20))
    
    def _generate_preview(self) -> str:
        """Generate command preview text."""
        lines = []
        burn = self.burn_var.get()
        
        lines.append("=" * 60)
        lines.append(f"{'BURN & CLOSE' if burn else 'CLOSE'} PREVIEW")
        lines.append("=" * 60)
        lines.append("")
        
        for i, address in enumerate(sorted(self.selected_addresses), 1):
            account = self._get_account_by_address(address)
            metadata = self.metadata_service.get(account.mint) if account else TokenMetadata()
            
            lines.append(f"[{i}] {metadata.display_symbol} - {metadata.display_name}")
            lines.append(f"    Address: {address}")
            
            if account:
                lines.append(f"    Balance: {account.balance}")
                lines.append(f"    Mint: {account.mint}")
            
            if burn:
                lines.append(f"    → spl-token burn {address} ALL")
            lines.append(f"    → spl-token close --address {address}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _generate_dry_run_report(self) -> str:
        """Generate dry run report."""
        lines = []
        burn = self.burn_var.get()
        count = len(self.selected_addresses)
        
        rent_total = count * Config.TOKEN_ACCOUNT_RENT
        fee_total = count * Config.TX_FEE * (2 if burn else 1)
        
        lines.append("=" * 60)
        lines.append("DRY RUN REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Accounts to close: {count}")
        lines.append(f"Operation: {'Burn & Close' if burn else 'Close only'}")
        lines.append("")
        lines.append("COST ANALYSIS:")
        lines.append(f"  Estimated rent recovery: {rent_total:.6f} SOL")
        lines.append(f"  Estimated fees: {fee_total:.6f} SOL")
        lines.append(f"  Net gain: {rent_total - fee_total:.6f} SOL")
        lines.append("")
        lines.append("ACCOUNTS:")
        lines.append("-" * 40)
        
        for address in sorted(self.selected_addresses):
            account = self._get_account_by_address(address)
            metadata = self.metadata_service.get(account.mint) if account else TokenMetadata()
            balance = account.balance if account else "?"
            lines.append(f"  {metadata.display_symbol:10} {balance:>15}  {address[:16]}...")
        
        return "\n".join(lines)
    
    def _close_selected(self) -> None:
        """Close selected accounts."""
        if not self.selected_addresses:
            messagebox.showwarning("No Selection", "Please select at least one account.")
            return
        
        count = len(self.selected_addresses)
        action = "burn and close" if self.burn_var.get() else "close"
        
        if not messagebox.askyesno(
            "Confirm Closure",
            f"Are you sure you want to {action} {count} account(s)?\n\n"
            "This action cannot be undone.\n"
            "SOL from rent will be returned to your wallet."
        ):
            return
        
        self.close_btn.config(state='disabled')
        self._log(f"Starting {action} of {count} accounts...")
        
        def execute():
            try:
                with self._lock:
                    addresses = list(self.selected_addresses)
                
                # Validate all addresses
                valid, invalid = SecurityUtils.validate_addresses(addresses)
                if invalid:
                    self._log_threadsafe(f"Skipping {len(invalid)} invalid addresses", LogLevel.WARNING)
                
                if not valid:
                    self._log_threadsafe("No valid addresses to process", LogLevel.ERROR)
                    return
                
                success = self._execute_batch_close(valid)
                
                if success:
                    self._log_threadsafe(f"✅ Successfully closed {len(valid)} accounts", LogLevel.SUCCESS)
                    with self._lock:
                        self.selected_addresses.clear()
                    self.root.after(0, self._refresh_accounts)
                else:
                    self._log_threadsafe("❌ Batch close failed", LogLevel.ERROR)
                    
            except Exception as ex:
                self._log_threadsafe(f"Error: {ex}", LogLevel.ERROR)
            finally:
                self.root.after(0, lambda: self.close_btn.config(state='normal'))
        
        threading.Thread(target=execute, daemon=True).start()
    
    def _execute_batch_close(self, addresses: List[str]) -> bool:
        """Execute batch close using a shell script."""
        burn = self.burn_var.get()
        
        # Build script content
        lines = ["#!/bin/bash", "set -e", ""]
        
        if burn:
            for addr in addresses:
                safe = SecurityUtils.sanitize_for_shell(addr)
                lines.append(f"spl-token burn {safe} ALL")
        
        for addr in addresses:
            safe = SecurityUtils.sanitize_for_shell(addr)
            lines.append(f"spl-token close --address {safe}")
        
        script_content = "\n".join(lines)
        
        # Execute via temp file
        fd, path = tempfile.mkstemp(suffix='.sh', text=True)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(script_content)
            
            os.chmod(path, 0o700)
            
            timeout = 30 + (len(addresses) * 15)
            result = CommandExecutor.run(['bash', path], timeout=timeout)
            
            return result.success
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# =============================================================================
# Web Interface
# =============================================================================

class WebInterface:
    """Web-based interface for the token closer."""
    
    HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Solana Token Account Closer</title>
    <style>
        :root {
            --bg-primary: #0f0f23;
            --bg-secondary: #1a1a2e;
            --bg-card: #16213e;
            --accent: #00d4ff;
            --accent-hover: #00a8cc;
            --danger: #ff4757;
            --danger-hover: #ff6b81;
            --success: #2ed573;
            --warning: #ffa502;
            --text-primary: #ffffff;
            --text-secondary: #a0a0b0;
            --border: #2a2a4a;
            --shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            --radius: 12px;
            --transition: all 0.2s ease;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, var(--bg-primary) 0%, var(--bg-secondary) 100%);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.6;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }
        
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 32px;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border);
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo-icon {
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, var(--accent), #9945ff);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
        }
        
        h1 {
            font-size: 1.75rem;
            font-weight: 700;
            background: linear-gradient(90deg, var(--accent), #9945ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .version {
            color: var(--text-secondary);
            font-size: 0.875rem;
        }
        
        .stats {
            display: flex;
            gap: 24px;
        }
        
        .stat-card {
            background: var(--bg-card);
            padding: 16px 24px;
            border-radius: var(--radius);
            border: 1px solid var(--border);
        }
        
        .stat-value {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--accent);
        }
        
        .stat-label {
            font-size: 0.75rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 16px;
        }
        
        .btn-group {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        
        button {
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.875rem;
            cursor: pointer;
            transition: var(--transition);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .btn-primary {
            background: var(--accent);
            color: var(--bg-primary);
        }
        
        .btn-primary:hover {
            background: var(--accent-hover);
            transform: translateY(-1px);
        }
        
        .btn-secondary {
            background: var(--bg-card);
            color: var(--text-primary);
            border: 1px solid var(--border);
        }
        
        .btn-secondary:hover {
            background: var(--border);
        }
        
        .btn-danger {
            background: var(--danger);
            color: white;
        }
        
        .btn-danger:hover {
            background: var(--danger-hover);
            transform: translateY(-1px);
        }
        
        .btn-danger:disabled {
            background: #555;
            cursor: not-allowed;
            transform: none;
        }
        
        .checkbox-label {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 16px;
            background: var(--bg-card);
            border-radius: 8px;
            border: 1px solid var(--border);
            cursor: pointer;
            transition: var(--transition);
        }
        
        .checkbox-label:hover {
            border-color: var(--warning);
        }
        
        .checkbox-label input {
            width: 18px;
            height: 18px;
            accent-color: var(--warning);
        }
        
        .table-container {
            background: var(--bg-card);
            border-radius: var(--radius);
            border: 1px solid var(--border);
            overflow: hidden;
            box-shadow: var(--shadow);
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: rgba(0, 212, 255, 0.1);
            padding: 16px;
            text-align: left;
            font-weight: 600;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--accent);
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            transition: var(--transition);
        }
        
        th:hover {
            background: rgba(0, 212, 255, 0.15);
        }
        
        td {
            padding: 14px 16px;
            border-bottom: 1px solid var(--border);
            font-size: 0.9rem;
        }
        
        tr:last-child td {
            border-bottom: none;
        }
        
        tr:hover {
            background: rgba(255, 255, 255, 0.02);
        }
        
        tr.selected {
            background: rgba(0, 212, 255, 0.1);
        }
        
        .checkbox-cell {
            width: 50px;
            text-align: center;
        }
        
        .checkbox-cell input {
            width: 18px;
            height: 18px;
            accent-color: var(--accent);
            cursor: pointer;
        }
        
        .symbol {
            font-weight: 700;
            color: var(--accent);
        }
        
        .token-link {
            color: var(--accent);
            text-decoration: none;
            transition: var(--transition);
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        
        .token-link:hover {
            color: #9945ff;
            text-decoration: underline;
        }
        
        .token-link::after {
            content: '↗';
            font-size: 0.7em;
            opacity: 0.6;
        }
        
        .balance {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            color: var(--success);
        }
        
        .address {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8rem;
            color: var(--text-secondary);
        }
        
        .address-wrap {
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        
        .copy-link {
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.8rem;
            line-height: 1;
            transition: var(--transition);
        }
        
        .copy-link:hover {
            color: var(--accent);
        }
        
        .log-panel {
            margin-top: 24px;
            background: var(--bg-card);
            border-radius: var(--radius);
            border: 1px solid var(--border);
            overflow: hidden;
        }
        
        .log-header {
            padding: 12px 16px;
            background: rgba(0, 0, 0, 0.2);
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .log-header h3 {
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--text-secondary);
        }
        
        .log-content {
            padding: 16px;
            max-height: 200px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8rem;
            line-height: 1.8;
        }
        
        .log-entry {
            padding: 4px 0;
        }
        
        .log-time {
            color: var(--text-secondary);
            margin-right: 8px;
        }
        
        .log-success { color: var(--success); }
        .log-error { color: var(--danger); }
        .log-warning { color: var(--warning); }
        .log-info { color: var(--text-primary); }
        
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            backdrop-filter: blur(4px);
        }
        
        .modal-overlay.active {
            display: flex;
        }
        
        .modal {
            background: var(--bg-card);
            border-radius: var(--radius);
            border: 1px solid var(--border);
            max-width: 700px;
            width: 90%;
            max-height: 80vh;
            overflow: hidden;
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.5);
        }
        
        .modal-header {
            padding: 20px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .modal-header h2 {
            font-size: 1.25rem;
        }
        
        .modal-close {
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0;
            line-height: 1;
        }
        
        .modal-close:hover {
            color: var(--text-primary);
        }
        
        .modal-body {
            padding: 24px;
            max-height: 60vh;
            overflow-y: auto;
        }
        
        .modal-body pre {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8rem;
            line-height: 1.6;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        
        .modal-footer {
            padding: 16px 24px;
            border-top: 1px solid var(--border);
            display: flex;
            justify-content: flex-end;
            gap: 12px;
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }
        
        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 16px;
        }
        
        .loading {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 40px;
            color: var(--text-secondary);
        }
        
        .spinner {
            width: 20px;
            height: 20px;
            border: 2px solid var(--border);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 16px 24px;
            box-shadow: var(--shadow);
            display: flex;
            align-items: center;
            gap: 12px;
            transform: translateY(100px);
            opacity: 0;
            transition: var(--transition);
            z-index: 2000;
        }
        
        .toast.show {
            transform: translateY(0);
            opacity: 1;
        }
        
        .toast.success { border-left: 4px solid var(--success); }
        .toast.error { border-left: 4px solid var(--danger); }
        .toast.warning { border-left: 4px solid var(--warning); }
        
        @media (max-width: 768px) {
            .container { padding: 16px; }
            header { flex-direction: column; gap: 16px; }
            .stats { flex-wrap: wrap; }
            .toolbar { flex-direction: column; }
            .btn-group { width: 100%; justify-content: center; }
            th, td { padding: 10px 8px; font-size: 0.8rem; }
            .address { display: none; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo">
                <div class="logo-icon">🔐</div>
                <div>
                    <h1>Solana Token Account Closer</h1>
                    <span class="version">v''' + Config.APP_VERSION + '''</span>
                </div>
            </div>
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value" id="account-count">-</div>
                    <div class="stat-label">Token Accounts</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="selected-count">0</div>
                    <div class="stat-label">Selected</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="rent-estimate">-</div>
                    <div class="stat-label">Est. Rent Recovery</div>
                </div>
            </div>
        </header>
        
        <div class="toolbar">
            <div class="btn-group">
                <button class="btn-primary" onclick="refreshAccounts()">
                    <span>⟳</span> Refresh
                </button>
                <button class="btn-secondary" onclick="selectAll()">
                    <span>☑</span> Select All
                </button>
                <button class="btn-secondary" onclick="deselectAll()">
                    <span>☐</span> Clear
                </button>
                <button class="btn-secondary" onclick="showPreview()">
                    <span>📋</span> Preview
                </button>
                <button class="btn-secondary" onclick="showDryRun()">
                    <span>🧪</span> Dry Run
                </button>
            </div>
            <div class="btn-group">
                <label class="checkbox-label">
                    <input type="checkbox" id="burn-checkbox">
                    <span>🔥 Burn tokens first</span>
                </label>
                <button class="btn-danger" id="close-btn" onclick="closeSelected()" disabled>
                    <span>🗑️</span> Close Selected
                </button>
            </div>
        </div>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th class="checkbox-cell">
                            <input type="checkbox" id="select-all-checkbox" onchange="toggleSelectAll()">
                        </th>
                        <th onclick="sortTable('symbol')">Ticker</th>
                        <th onclick="sortTable('name')">Token Name</th>
                        <th onclick="sortTable('balance')">Balance</th>
                        <th onclick="sortTable('address')">Account</th>
                        <th onclick="sortTable('mint')">Mint</th>
                    </tr>
                </thead>
                <tbody id="accounts-table">
                    <tr>
                        <td colspan="6">
                            <div class="loading">
                                <div class="spinner"></div>
                                <span>Loading accounts...</span>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
        
        <div class="log-panel">
            <div class="log-header">
                <h3>Activity Log</h3>
                <button class="btn-secondary" onclick="clearLog()" style="padding: 6px 12px; font-size: 0.75rem;">Clear</button>
            </div>
            <div class="log-content" id="log-content"></div>
        </div>
    </div>
    
    <div class="modal-overlay" id="modal-overlay" onclick="closeModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2 id="modal-title">Modal Title</h2>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <pre id="modal-content"></pre>
            </div>
            <div class="modal-footer">
                <button class="btn-secondary" onclick="closeModal()">Close</button>
            </div>
        </div>
    </div>
    
    <div class="modal-overlay" id="confirm-overlay">
        <div class="modal">
            <div class="modal-header">
                <h2>Confirm Action</h2>
                <button class="modal-close" onclick="closeConfirm()">&times;</button>
            </div>
            <div class="modal-body">
                <p id="confirm-message"></p>
            </div>
            <div class="modal-footer">
                <button class="btn-secondary" onclick="closeConfirm()">Cancel</button>
                <button class="btn-danger" id="confirm-btn" onclick="confirmAction()">Confirm</button>
            </div>
        </div>
    </div>
    
    <div class="toast" id="toast"></div>
    
    <script>
        let accounts = [];
        let selectedAddresses = new Set();
        let pendingAction = null;
        
        function log(message, level = 'info') {
            const logContent = document.getElementById('log-content');
            const time = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = 'log-entry log-' + level;
            entry.innerHTML = '<span class="log-time">[' + time + ']</span>' + message;
            logContent.appendChild(entry);
            logContent.scrollTop = logContent.scrollHeight;
        }
        
        function showToast(message, type = 'info') {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast show ' + type;
            setTimeout(() => toast.className = 'toast', 3000);
        }
        
        function updateStats() {
            document.getElementById('account-count').textContent = accounts.length;
            document.getElementById('selected-count').textContent = selectedAddresses.size;
            const rentEstimate = selectedAddresses.size * ''' + str(Config.TOKEN_ACCOUNT_RENT) + ''';
            document.getElementById('rent-estimate').textContent = rentEstimate.toFixed(4) + ' SOL';
            
            const closeBtn = document.getElementById('close-btn');
            const burn = document.getElementById('burn-checkbox').checked;
            if (selectedAddresses.size > 0) {
                closeBtn.disabled = false;
                closeBtn.innerHTML = (burn ? '<span>🔥</span> Burn & Close ' : '<span>🗑️</span> Close ') + selectedAddresses.size;
            } else {
                closeBtn.disabled = true;
                closeBtn.innerHTML = '<span>🗑️</span> Close Selected';
            }
        }
        
        function renderAccounts() {
            const tbody = document.getElementById('accounts-table');
            if (accounts.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state"><div class="empty-state-icon">📭</div><p>No token accounts found</p></div></td></tr>';
                return;
            }
            
            tbody.innerHTML = accounts.map(acc => {
                const selected = selectedAddresses.has(acc.address);
                const symbol = acc.symbol || '—';
                const tokenLink = 'https://birdeye.so/token/' + acc.mint + '?chain=solana';
                const symbolHtml = symbol !== '—' 
                    ? '<a href="' + tokenLink + '" target="_blank" rel="noopener" class="token-link" title="View on Birdeye">' + symbol + '</a>'
                    : symbol;
                const accountCell = '<span class="address-wrap">' +
                    '<span>' + truncateAddress(acc.address) + '</span>' +
                    '<a href="#" class="copy-link" onclick="copyAddress(event, \\'' + acc.address + '\\', \'Account address\')" title="Copy full account address">📋</a>' +
                    '</span>';
                const mintCell = '<span class="address-wrap">' +
                    '<span>' + truncateAddress(acc.mint) + '</span>' +
                    '<a href="#" class="copy-link" onclick="copyAddress(event, \\'' + acc.mint + '\\', \'Contract address\')" title="Copy full contract address">📋</a>' +
                    '</span>';
                return '<tr class="' + (selected ? 'selected' : '') + '" data-address="' + acc.address + '">' +
                    '<td class="checkbox-cell"><input type="checkbox" ' + (selected ? 'checked' : '') + ' onchange="toggleAccount(\\'' + acc.address + '\\')"></td>' +
                    '<td class="symbol">' + symbolHtml + '</td>' +
                    '<td>' + (acc.name || '—') + '</td>' +
                    '<td class="balance">' + acc.balance + '</td>' +
                    '<td class="address">' + accountCell + '</td>' +
                    '<td class="address">' + mintCell + '</td>' +
                '</tr>';
            }).join('');
            
            updateStats();
        }
        
        function truncateAddress(addr) {
            if (addr.length > 20) {
                return addr.substring(0, 8) + '...' + addr.substring(addr.length - 6);
            }
            return addr;
        }
        
        async function copyAddress(event, value, label) {
            event.preventDefault();
            try {
                await navigator.clipboard.writeText(value);
                showToast(label + ' copied', 'success');
            } catch (err) {
                showToast('Failed to copy address', 'error');
            }
        }
        
        let metadataFetchInProgress = false;
        
        async function refreshAccounts() {
            log('Refreshing token accounts...', 'info');
            document.getElementById('accounts-table').innerHTML = '<tr><td colspan="6"><div class="loading"><div class="spinner"></div><span>Loading accounts...</span></div></td></tr>';
            
            try {
                const response = await fetch('/api/accounts');
                const data = await response.json();
                if (data.success) {
                    accounts = data.accounts;
                    renderAccounts();
                    log('Found ' + accounts.length + ' token accounts', 'success');
                    showToast('Loaded ' + accounts.length + ' accounts', 'success');
                    
                    if (data.missing_metadata > 0) {
                        log('Fetching metadata for ' + data.missing_metadata + ' tokens...', 'info');
                        fetchMetadataInBackground();
                    }
                } else {
                    log('Error: ' + data.error, 'error');
                    showToast('Failed to load accounts', 'error');
                }
            } catch (err) {
                log('Error: ' + err.message, 'error');
                showToast('Connection error', 'error');
            }
        }
        
        async function fetchMetadataInBackground() {
            if (metadataFetchInProgress) return;
            metadataFetchInProgress = true;
            
            try {
                while (true) {
                    const response = await fetch('/api/metadata');
                    const data = await response.json();
                    
                    if (data.success) {
                        accounts = data.accounts;
                        renderAccounts();
                        
                        if (data.remaining === 0) {
                            if (data.fetched > 0) {
                                log('Metadata loading complete', 'success');
                            }
                            break;
                        }
                    } else {
                        break;
                    }
                    
                    await new Promise(resolve => setTimeout(resolve, 100));
                }
            } catch (err) {
                log('Metadata fetch error: ' + err.message, 'warning');
            } finally {
                metadataFetchInProgress = false;
            }
        }
        
        function toggleAccount(address) {
            if (selectedAddresses.has(address)) {
                selectedAddresses.delete(address);
            } else {
                selectedAddresses.add(address);
            }
            renderAccounts();
        }
        
        function toggleSelectAll() {
            const checkbox = document.getElementById('select-all-checkbox');
            if (checkbox.checked) {
                selectAll();
            } else {
                deselectAll();
            }
        }
        
        function selectAll() {
            accounts.forEach(acc => selectedAddresses.add(acc.address));
            document.getElementById('select-all-checkbox').checked = true;
            renderAccounts();
            log('Selected ' + selectedAddresses.size + ' accounts', 'info');
        }
        
        function deselectAll() {
            selectedAddresses.clear();
            document.getElementById('select-all-checkbox').checked = false;
            renderAccounts();
            log('Cleared selection', 'info');
        }
        
        async function showPreview() {
            if (selectedAddresses.size === 0) {
                showToast('Please select at least one account', 'warning');
                return;
            }
            
            const burn = document.getElementById('burn-checkbox').checked;
            try {
                const response = await fetch('/api/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ addresses: Array.from(selectedAddresses), burn: burn })
                });
                const data = await response.json();
                openModal('Command Preview', data.preview);
            } catch (err) {
                showToast('Error generating preview', 'error');
            }
        }
        
        async function showDryRun() {
            if (selectedAddresses.size === 0) {
                showToast('Please select at least one account', 'warning');
                return;
            }
            
            const burn = document.getElementById('burn-checkbox').checked;
            try {
                const response = await fetch('/api/dry-run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ addresses: Array.from(selectedAddresses), burn: burn })
                });
                const data = await response.json();
                openModal('Dry Run Report', data.report);
            } catch (err) {
                showToast('Error generating report', 'error');
            }
        }
        
        function closeSelected() {
            if (selectedAddresses.size === 0) return;
            
            const burn = document.getElementById('burn-checkbox').checked;
            const action = burn ? 'burn and close' : 'close';
            document.getElementById('confirm-message').innerHTML = 
                'Are you sure you want to <strong>' + action + '</strong> ' + selectedAddresses.size + ' account(s)?<br><br>' +
                '<span style="color: var(--warning);">⚠️ This action cannot be undone.</span><br>' +
                'SOL from rent will be returned to your wallet.';
            
            pendingAction = async () => {
                log('Starting ' + action + ' of ' + selectedAddresses.size + ' accounts...', 'info');
                document.getElementById('close-btn').disabled = true;
                
                try {
                    const response = await fetch('/api/close', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ addresses: Array.from(selectedAddresses), burn: burn })
                    });
                    const data = await response.json();
                    
                    if (data.success) {
                        log('✅ Successfully closed ' + selectedAddresses.size + ' accounts', 'success');
                        showToast('Accounts closed successfully', 'success');
                        selectedAddresses.clear();
                        refreshAccounts();
                    } else {
                        log('❌ Error: ' + data.error, 'error');
                        showToast('Failed to close accounts', 'error');
                    }
                } catch (err) {
                    log('❌ Error: ' + err.message, 'error');
                    showToast('Connection error', 'error');
                }
                
                document.getElementById('close-btn').disabled = false;
            };
            
            document.getElementById('confirm-overlay').classList.add('active');
        }
        
        function confirmAction() {
            closeConfirm();
            if (pendingAction) {
                pendingAction();
                pendingAction = null;
            }
        }
        
        function closeConfirm() {
            document.getElementById('confirm-overlay').classList.remove('active');
        }
        
        function openModal(title, content) {
            document.getElementById('modal-title').textContent = title;
            document.getElementById('modal-content').textContent = content;
            document.getElementById('modal-overlay').classList.add('active');
        }
        
        function closeModal(event) {
            if (!event || event.target === document.getElementById('modal-overlay')) {
                document.getElementById('modal-overlay').classList.remove('active');
            }
        }
        
        function clearLog() {
            document.getElementById('log-content').innerHTML = '';
        }
        
        function sortTable(column) {
            accounts.sort((a, b) => {
                const valA = (a[column] || '').toString().toLowerCase();
                const valB = (b[column] || '').toString().toLowerCase();
                return valA.localeCompare(valB, undefined, {numeric: true});
            });
            renderAccounts();
        }
        
        document.getElementById('burn-checkbox').addEventListener('change', function() {
            if (this.checked) {
                log('🔥 Burn before close enabled', 'warning');
            }
            updateStats();
        });
        
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeModal();
                closeConfirm();
            }
        });
        
        refreshAccounts();
        log('Web interface initialized', 'success');
    </script>
</body>
</html>'''
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.metadata_service = MetadataService()
        self.accounts: List[TokenAccount] = []
        self._lock = threading.Lock()
        self.server: Optional[HTTPServer] = None
    
    def start(self, open_browser: bool = True) -> None:
        """Start the web server (blocking) and optionally open browser."""
        self._load_metadata()
        
        handler = self._create_handler()
        self.server = HTTPServer(('127.0.0.1', self.port), handler)
        
        url = f"http://127.0.0.1:{self.port}"
        print(f"\n{'='*50}")
        print(f"  Solana Token Account Closer - Web Interface")
        print(f"{'='*50}")
        print(f"\n  Server running at: {url}")
        print(f"  Press Ctrl+C to stop\n")
        
        if open_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.server.shutdown()
    
    def start_background(self, open_browser: bool = True) -> None:
        """Start the web server in a background thread."""
        self._load_metadata()
        
        handler = self._create_handler()
        self.server = HTTPServer(('127.0.0.1', self.port), handler)
        
        url = f"http://127.0.0.1:{self.port}"
        print(f"\n  Web interface also available at: {url}")
        
        def serve():
            try:
                self.server.serve_forever()
            except Exception:
                pass
        
        self._server_thread = threading.Thread(target=serve, daemon=True)
        self._server_thread.start()
        
        if open_browser:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    
    def stop(self) -> None:
        """Stop the web server."""
        if self.server:
            self.server.shutdown()
    
    def _load_metadata(self) -> None:
        """Load token metadata."""
        print("Loading token metadata...")
        
        event = threading.Event()
        result = {'success': False, 'message': ''}
        
        def callback(success: bool, message: str):
            result['success'] = success
            result['message'] = message
            event.set()
        
        self.metadata_service.load_from_api(callback)
        event.wait(timeout=20)
        
        if result['success']:
            print(f"  {result['message']}")
        else:
            print(f"  Warning: {result['message']}")
    
    def _fetch_accounts(self) -> Tuple[bool, str]:
        """Fetch token accounts (without blocking on metadata)."""
        result = CommandExecutor.run(['spl-token', 'accounts', '--output', 'json'])
        
        if not result.success:
            return False, result.error
        
        try:
            data = json.loads(result.output)
            accounts = [TokenAccount.from_json(a) for a in data.get('accounts', [])]
            
            with self._lock:
                self.accounts = accounts
            
            return True, ""
        except json.JSONDecodeError as e:
            return False, str(e)
    
    def _get_missing_mints(self) -> List[str]:
        """Get list of mints without metadata."""
        with self._lock:
            return [a.mint for a in self.accounts if not self.metadata_service.has(a.mint)]
    
    def _fetch_metadata_batch(self, mints: List[str], batch_size: int = 3) -> int:
        """Fetch metadata for a batch of mints. Returns count fetched."""
        to_fetch = [m for m in mints[:batch_size] if SecurityUtils.is_valid_solana_address(m)]
        fetched = 0
        
        for mint in to_fetch:
            # Try sources in order: CLI -> DexScreener -> Metaplex (on-chain)
            metadata = (
                self.metadata_service.fetch_from_cli(mint) or
                self.metadata_service.fetch_from_dexscreener(mint) or
                self.metadata_service.fetch_from_metaplex(mint)
            )
            if metadata:
                self.metadata_service.set(mint, metadata)
                fetched += 1
        
        return fetched
    
    def _get_accounts_json(self) -> List[dict]:
        """Get accounts as JSON-serializable list."""
        with self._lock:
            result = []
            for acc in self.accounts:
                meta = self.metadata_service.get(acc.mint)
                result.append({
                    'address': acc.address,
                    'mint': acc.mint,
                    'balance': acc.balance,
                    'decimals': acc.decimals,
                    'symbol': meta.display_symbol,
                    'name': meta.display_name,
                })
            return result
    
    def _generate_preview(self, addresses: List[str], burn: bool) -> str:
        """Generate command preview."""
        lines = []
        lines.append("=" * 60)
        lines.append(f"{'BURN & CLOSE' if burn else 'CLOSE'} PREVIEW")
        lines.append("=" * 60)
        lines.append("")
        
        for i, address in enumerate(sorted(addresses), 1):
            account = None
            with self._lock:
                for acc in self.accounts:
                    if acc.address == address:
                        account = acc
                        break
            
            meta = self.metadata_service.get(account.mint) if account else TokenMetadata()
            
            lines.append(f"[{i}] {meta.display_symbol} - {meta.display_name}")
            lines.append(f"    Address: {address}")
            
            if account:
                lines.append(f"    Balance: {account.balance}")
                lines.append(f"    Mint: {account.mint}")
            
            if burn:
                lines.append(f"    → spl-token burn {address} ALL")
            lines.append(f"    → spl-token close --address {address}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _generate_dry_run(self, addresses: List[str], burn: bool) -> str:
        """Generate dry run report."""
        lines = []
        count = len(addresses)
        
        rent_total = count * Config.TOKEN_ACCOUNT_RENT
        fee_total = count * Config.TX_FEE * (2 if burn else 1)
        
        lines.append("=" * 60)
        lines.append("DRY RUN REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Accounts to close: {count}")
        lines.append(f"Operation: {'Burn & Close' if burn else 'Close only'}")
        lines.append("")
        lines.append("COST ANALYSIS:")
        lines.append(f"  Estimated rent recovery: {rent_total:.6f} SOL")
        lines.append(f"  Estimated fees: {fee_total:.6f} SOL")
        lines.append(f"  Net gain: {rent_total - fee_total:.6f} SOL")
        lines.append("")
        lines.append("ACCOUNTS:")
        lines.append("-" * 40)
        
        for address in sorted(addresses):
            account = None
            with self._lock:
                for acc in self.accounts:
                    if acc.address == address:
                        account = acc
                        break
            
            meta = self.metadata_service.get(account.mint) if account else TokenMetadata()
            balance = account.balance if account else "?"
            lines.append(f"  {meta.display_symbol:10} {balance:>15}  {address[:16]}...")
        
        return "\n".join(lines)
    
    def _close_accounts(self, addresses: List[str], burn: bool) -> Tuple[bool, str]:
        """Close token accounts."""
        valid, invalid = SecurityUtils.validate_addresses(addresses)
        if not valid:
            return False, "No valid addresses"
        
        lines = ["#!/bin/bash", "set -e", ""]
        
        if burn:
            for addr in valid:
                safe = SecurityUtils.sanitize_for_shell(addr)
                lines.append(f"spl-token burn {safe} ALL")
        
        for addr in valid:
            safe = SecurityUtils.sanitize_for_shell(addr)
            lines.append(f"spl-token close --address {safe}")
        
        script_content = "\n".join(lines)
        
        fd, path = tempfile.mkstemp(suffix='.sh', text=True)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(script_content)
            
            os.chmod(path, 0o700)
            
            timeout = 30 + (len(valid) * 15)
            result = CommandExecutor.run(['bash', path], timeout=timeout)
            
            return result.success, result.error if not result.success else ""
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    
    def _create_handler(self):
        """Create HTTP request handler."""
        web_interface = self
        
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            
            def send_json(self, data: dict, status: int = 200):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            
            def do_GET(self):
                parsed = urlparse(self.path)
                
                if parsed.path == '/':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.end_headers()
                    self.wfile.write(WebInterface.HTML_TEMPLATE.encode())
                
                elif parsed.path == '/api/accounts':
                    success, error = web_interface._fetch_accounts()
                    if success:
                        missing = web_interface._get_missing_mints()
                        self.send_json({
                            'success': True,
                            'accounts': web_interface._get_accounts_json(),
                            'missing_metadata': len(missing)
                        })
                    else:
                        self.send_json({'success': False, 'error': error}, 500)
                
                elif parsed.path == '/api/metadata':
                    missing = web_interface._get_missing_mints()
                    if missing:
                        fetched = web_interface._fetch_metadata_batch(missing, batch_size=3)
                        remaining = len(missing) - fetched
                    else:
                        fetched = 0
                        remaining = 0
                    
                    self.send_json({
                        'success': True,
                        'accounts': web_interface._get_accounts_json(),
                        'fetched': fetched,
                        'remaining': remaining
                    })
                
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def do_POST(self):
                parsed = urlparse(self.path)
                content_length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(content_length)) if content_length else {}
                
                addresses = body.get('addresses', [])
                burn = body.get('burn', False)
                
                if parsed.path == '/api/preview':
                    preview = web_interface._generate_preview(addresses, burn)
                    self.send_json({'preview': preview})
                
                elif parsed.path == '/api/dry-run':
                    report = web_interface._generate_dry_run(addresses, burn)
                    self.send_json({'report': report})
                
                elif parsed.path == '/api/close':
                    success, error = web_interface._close_accounts(addresses, burn)
                    if success:
                        self.send_json({'success': True})
                    else:
                        self.send_json({'success': False, 'error': error}, 500)
                
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.end_headers()
        
        return Handler


# =============================================================================
# Entry Point
# =============================================================================

def main():
    """Application entry point."""
    parser = argparse.ArgumentParser(
        description=f"{Config.APP_NAME} - Close empty Solana token accounts and recover rent"
    )
    parser.add_argument(
        '--web', '-w',
        action='store_true',
        help='Launch web interface instead of desktop GUI'
    )
    parser.add_argument(
        '--both', '-b',
        action='store_true',
        help='Launch both desktop GUI and web interface simultaneously'
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=8080,
        help='Port for web interface (default: 8080)'
    )
    parser.add_argument(
        '--no-browser',
        action='store_true',
        help='Do not automatically open browser (web mode only)'
    )
    
    args = parser.parse_args()
    
    # Check prerequisites
    if not CommandExecutor.check_spl_token_available():
        print("\n❌ Error: spl-token CLI not found.\n")
        print("Please install Solana CLI tools:")
        print('  sh -c "$(curl -sSfL https://release.solana.com/stable/install)"')
        return
    
    web_server = None
    
    if args.both:
        # Run both interfaces
        if not TKINTER_AVAILABLE:
            print("\n❌ Error: tkinter not available for desktop GUI.")
            print("Use --web flag for web interface only.")
            return
        
        # Start web server in background thread
        web_server = WebInterface(port=args.port)
        web_server.start_background(open_browser=not args.no_browser)
        
        # Run desktop GUI in main thread
        root = tk.Tk()
        app = TokenAccountCloser(root)
        
        def on_close():
            if messagebox.askokcancel("Quit", "Are you sure you want to quit?"):
                if web_server:
                    web_server.stop()
                root.destroy()
        
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
        
    elif args.web:
        # Web interface only
        web_server = WebInterface(port=args.port)
        web_server.start(open_browser=not args.no_browser)
    else:
        # Desktop GUI only
        if not TKINTER_AVAILABLE:
            print("\n❌ Error: tkinter not available.")
            print("Use --web flag for web interface instead:")
            print(f"  python {__file__} --web")
            return
        
        root = tk.Tk()
        app = TokenAccountCloser(root)
        
        def on_close():
            if messagebox.askokcancel("Quit", "Are you sure you want to quit?"):
                root.destroy()
        
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()


if __name__ == "__main__":
    main()
