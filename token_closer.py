#!/usr/bin/env python3
"""
Solana Token Account Closer
A GUI application to close Solana token accounts and recover rent.

Security: Input validation, shell sanitization, secure temp files
Architecture: Separated concerns with dataclasses and type hints
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


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
    
    def fetch_missing(self, mints: Set[str], progress_callback: Callable[[int, int], None]) -> int:
        """Fetch metadata for mints not in cache. Returns count fetched."""
        to_fetch = [m for m in mints if not self.has(m) and SecurityUtils.is_valid_solana_address(m)]
        
        if not to_fetch:
            return 0
        
        fetched = 0
        for i, mint in enumerate(to_fetch):
            # Try CLI first, then DexScreener
            metadata = self.fetch_from_cli(mint) or self.fetch_from_dexscreener(mint)
            
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
        
        # Columns configuration
        columns = ('select', 'symbol', 'name', 'balance', 'address', 'mint', 'dec')
        self.tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse')
        
        # Column headings and widths
        col_config = [
            ('select', '✓', 40, 'center'),
            ('symbol', 'Ticker', 90, 'w'),
            ('name', 'Token Name', 200, 'w'),
            ('balance', 'Balance', 130, 'e'),
            ('address', 'Account', 160, 'w'),
            ('mint', 'Mint', 160, 'w'),
            ('dec', 'Dec', 45, 'center'),
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
# Entry Point
# =============================================================================

def main():
    """Application entry point."""
    # Check prerequisites
    if not CommandExecutor.check_spl_token_available():
        messagebox.showerror(
            "Missing Dependency",
            "spl-token CLI not found.\n\n"
            "Please install Solana CLI tools:\n"
            "sh -c \"$(curl -sSfL https://release.solana.com/stable/install)\""
        )
        return
    
    # Create application
    root = tk.Tk()
    app = TokenAccountCloser(root)
    
    # Handle close
    def on_close():
        if messagebox.askokcancel("Quit", "Are you sure you want to quit?"):
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
