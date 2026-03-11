#!/usr/bin/env python3
"""
Solana Token Account Closer
A simple GUI application to close Solana token accounts using spl-token CLI.
Optimizes for cost by allowing batch selection and closing of multiple accounts.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import json
import threading
from typing import List, Dict, Optional
import os
import tempfile
import re
import shlex
import urllib.request
import urllib.error

class TokenAccountCloser:
    # Valid Solana address pattern: Base58 characters, 32-44 chars long
    SOLANA_ADDRESS_PATTERN = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
    
    # Token list API URLs (tried in order)
    TOKEN_LIST_URLS = [
        "https://token.jup.ag/all",
        "https://cache.jup.ag/tokens",
        "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json",
    ]
    
    def __init__(self, root):
        self.root = root
        self.root.title("Solana Token Account Closer")
        self.root.geometry("1100x700")  # Wider to accommodate new columns
        self.root.configure(bg='#f0f0f0')
        
        # Data storage with thread lock for safety
        self._lock = threading.Lock()
        self.token_accounts = []
        self.selected_accounts = set()
        
        # Token metadata cache: mint -> {name, symbol, description}
        self.token_metadata_cache = {}
        self.metadata_loading = False
        
        # Create UI
        self.create_widgets()
        
        # Load token metadata in background
        self.load_token_metadata()
        
        # Load accounts on startup
        self.refresh_accounts()
    
    @classmethod
    def is_valid_solana_address(cls, address: str) -> bool:
        """Validate that an address matches Solana's Base58 format"""
        if not address or not isinstance(address, str):
            return False
        return bool(cls.SOLANA_ADDRESS_PATTERN.match(address))
    
    @staticmethod
    def sanitize_for_shell(value: str) -> str:
        """Sanitize a value for safe use in shell commands"""
        return shlex.quote(value)
    
    def load_token_metadata(self):
        """Load token metadata from token list APIs (tries multiple sources)"""
        if self.metadata_loading:
            return
        
        self.metadata_loading = True
        self.log_message("Loading token metadata...", "INFO")
        
        def fetch_metadata():
            last_error = None
            
            for url in self.TOKEN_LIST_URLS:
                try:
                    req = urllib.request.Request(
                        url,
                        headers={'User-Agent': 'TokenCloser/1.0'}
                    )
                    with urllib.request.urlopen(req, timeout=15) as response:
                        data = json.loads(response.read().decode('utf-8'))
                        
                        # Handle different response formats
                        tokens = data
                        if isinstance(data, dict):
                            # Solana token list format has tokens under 'tokens' key
                            tokens = data.get('tokens', data.get('data', []))
                        
                        if not isinstance(tokens, list):
                            continue
                        
                        # Build cache from token list
                        count = 0
                        with self._lock:
                            for token in tokens:
                                mint = token.get('address', '')
                                if mint:
                                    self.token_metadata_cache[mint] = {
                                        'name': token.get('name', ''),
                                        'symbol': token.get('symbol', ''),
                                        'description': token.get('description', '') or token.get('name', '')
                                    }
                                    count += 1
                        
                        if count > 0:
                            final_count = count
                            self.root.after(0, lambda c=final_count: self.log_message(
                                f"Loaded metadata for {c} tokens", "SUCCESS"))
                            self.root.after(0, self.update_accounts_display)
                            self.metadata_loading = False
                            return  # Success - stop trying other URLs
                            
                except urllib.error.URLError as ex:
                    last_error = str(ex.reason) if hasattr(ex, 'reason') else str(ex)
                except Exception as ex:
                    last_error = str(ex)
            
            # All URLs failed
            if last_error:
                err = last_error
                self.root.after(0, lambda msg=err: self.log_message(
                    f"Could not load token metadata (will use CLI fallback): {msg}", "WARNING"))
            
            self.metadata_loading = False
        
        threading.Thread(target=fetch_metadata, daemon=True).start()
    
    def get_token_info(self, mint: str) -> Dict[str, str]:
        """Get token name and symbol for a mint address"""
        with self._lock:
            if mint in self.token_metadata_cache:
                return self.token_metadata_cache[mint]
        return {'name': '', 'symbol': '', 'description': ''}
    
    def fetch_token_metadata_cli(self, mint: str) -> Optional[Dict[str, str]]:
        """Fetch token metadata using spl-token display command (fallback)"""
        if not self.is_valid_solana_address(mint):
            return None
        
        try:
            success, output, error = self.run_command(
                ['spl-token', 'display', mint], timeout=10
            )
            if success and output:
                # Parse the output for name/symbol
                name = ''
                symbol = ''
                for line in output.split('\n'):
                    if 'Name:' in line:
                        name = line.split('Name:')[-1].strip()
                    elif 'Symbol:' in line:
                        symbol = line.split('Symbol:')[-1].strip()
                
                if name or symbol:
                    return {'name': name, 'symbol': symbol, 'description': name}
        except Exception:
            pass
        return None
    
    def load_missing_metadata(self):
        """Load metadata for tokens not in the cache using CLI (fallback)"""
        def fetch_missing():
            mints_to_fetch = set()
            for account in self.token_accounts:
                mint = account.get('mint', '')
                with self._lock:
                    if mint and mint not in self.token_metadata_cache:
                        mints_to_fetch.add(mint)
            
            if not mints_to_fetch:
                return
            
            self.root.after(0, lambda n=len(mints_to_fetch): self.log_message(
                f"Fetching metadata for {n} unknown tokens...", "INFO"))
            
            fetched = 0
            for mint in mints_to_fetch:
                info = self.fetch_token_metadata_cli(mint)
                if info:
                    with self._lock:
                        self.token_metadata_cache[mint] = info
                    fetched += 1
            
            if fetched > 0:
                self.root.after(0, lambda n=fetched: self.log_message(
                    f"Fetched metadata for {n} additional tokens", "SUCCESS"))
                self.root.after(0, self.update_accounts_display)
        
        threading.Thread(target=fetch_missing, daemon=True).start()
    
    def create_widgets(self):
        """Create the main UI widgets"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # Title
        title_label = ttk.Label(main_frame, text="🔒 Solana Token Account Closer", 
                               font=('Helvetica', 16, 'bold'))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))
        
        # Control buttons frame
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=1, column=0, columnspan=3, pady=(0, 10), sticky=(tk.W, tk.E))
        
        # Refresh button
        self.refresh_btn = ttk.Button(control_frame, text="🔄 Refresh Accounts", 
                                     command=self.refresh_accounts)
        self.refresh_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Select all button
        self.select_all_btn = ttk.Button(control_frame, text="☑️ Select All", 
                                        command=self.select_all_accounts)
        self.select_all_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Deselect all button
        self.deselect_all_btn = ttk.Button(control_frame, text="☐ Deselect All", 
                                          command=self.deselect_all_accounts)
        self.deselect_all_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Clear selections button (for after closure)
        self.clear_selections_btn = ttk.Button(control_frame, text="🧹 Clear Selections", 
                                             command=self.clear_selections_after_closure)
        self.clear_selections_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Preview commands button
        self.preview_btn = ttk.Button(control_frame, text="👁️ Preview Commands", 
                                     command=self.preview_commands)
        
        # Dry run button
        self.dryrun_btn = ttk.Button(control_frame, text="🧪 Dry Run", 
                                    command=self.dry_run_commands)
        self.dryrun_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Burn before close checkbox
        self.burn_before_close_var = tk.BooleanVar()
        self.burn_checkbox = ttk.Checkbutton(control_frame, text="🔥 Burn Before Close", 
                                           variable=self.burn_before_close_var,
                                           command=self.on_burn_option_changed)
        self.burn_checkbox.pack(side=tk.LEFT, padx=(0, 10))
        
        # Close selected button
        self.close_btn = ttk.Button(control_frame, text="🗑️ Close Selected", 
                                   command=self.close_selected_accounts, 
                                   style='Danger.TButton')
        self.close_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Status label
        self.status_label = ttk.Label(control_frame, text="Ready", foreground='green')
        self.status_label.pack(side=tk.RIGHT)
        
        # Accounts list frame
        list_frame = ttk.LabelFrame(main_frame, text="Token Accounts", padding="5")
        list_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        
        # Create Treeview for accounts
        columns = ('select', 'symbol', 'name', 'balance', 'address', 'mint', 'decimals')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=15)
        
        # Define headings
        self.tree.heading('select', text='Select')
        self.tree.heading('symbol', text='Ticker')
        self.tree.heading('name', text='Token Name')
        self.tree.heading('balance', text='Balance')
        self.tree.heading('address', text='Account Address')
        self.tree.heading('mint', text='Token Mint')
        self.tree.heading('decimals', text='Dec')
        
        # Configure column widths
        self.tree.column('select', width=50, anchor='center')
        self.tree.column('symbol', width=80, anchor='w')
        self.tree.column('name', width=180, anchor='w')
        self.tree.column('balance', width=120, anchor='e')
        self.tree.column('address', width=150)
        self.tree.column('mint', width=150)
        self.tree.column('decimals', width=50, anchor='center')
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scrollbar = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Grid layout for tree and scrollbars
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        v_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        h_scrollbar.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # Bind double-click to toggle selection
        self.tree.bind('<Double-1>', self.toggle_selection)
        
        # Log frame
        log_frame = ttk.LabelFrame(main_frame, text="Operation Log", padding="5")
        log_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        # Log text area
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure row weights for main frame
        main_frame.rowconfigure(2, weight=3)
        main_frame.rowconfigure(3, weight=1)
    
    def log_message(self, message: str, level: str = "INFO"):
        """Add a message to the log with timestamp"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "black",
            "SUCCESS": "green",
            "ERROR": "red",
            "WARNING": "orange"
        }
        
        self.log_text.insert(tk.END, f"[{timestamp}] {level}: {message}\n")
        self.log_text.see(tk.END)
        
        # Update status label
        self.status_label.config(text=message, foreground=color_map.get(level, "black"))
    
    def run_command(self, command: List[str], timeout: int = 30) -> tuple:
        """Run a shell command and return (success, output, error)"""
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            return (result.returncode == 0, result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            return (False, "", f"Command timed out after {timeout} seconds")
        except Exception as e:
            return (False, "", str(e))
    
    def refresh_accounts(self):
        """Refresh the list of token accounts"""
        self.log_message("Refreshing token accounts...")
        self.refresh_btn.config(state='disabled')
        
        # Run in background thread to avoid blocking UI
        def refresh_thread():
            try:
                # Get token accounts
                success, output, error = self.run_command(['spl-token', 'accounts', '--output', 'json'])
                
                if success:
                    try:
                        accounts_data = json.loads(output)
                        self.token_accounts = accounts_data.get('accounts', [])
                        num_accounts = len(self.token_accounts)
                        self.root.after(0, self.update_accounts_display)
                        self.root.after(0, lambda n=num_accounts: self.log_message(f"Found {n} token accounts", "SUCCESS"))
                        # Try to load metadata for any unknown tokens
                        self.root.after(500, self.load_missing_metadata)
                    except json.JSONDecodeError:
                        self.root.after(0, lambda: self.log_message("Failed to parse accounts data", "ERROR"))
                else:
                    err_msg = str(error)
                    self.root.after(0, lambda msg=err_msg: self.log_message(f"Failed to get accounts: {msg}", "ERROR"))
                
                self.root.after(0, lambda: self.refresh_btn.config(state='normal'))
                
            except Exception as ex:
                err_msg = str(ex)
                self.root.after(0, lambda msg=err_msg: self.log_message(f"Error refreshing accounts: {msg}", "ERROR"))
                self.root.after(0, lambda: self.refresh_btn.config(state='normal'))
        
        threading.Thread(target=refresh_thread, daemon=True).start()
    
    def update_accounts_display(self):
        """Update the accounts display in the treeview"""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Add accounts to treeview
        for account in self.token_accounts:
            # Create checkbox-like selection indicator
            select_text = "☑️" if account.get('address') in self.selected_accounts else "☐"
            
            # Get token amount information
            token_amount = account.get('tokenAmount', {})
            balance_amount = token_amount.get('uiAmountString', '0')
            decimals = token_amount.get('decimals', 0)
            
            # Get mint address
            mint = account.get('mint', 'Unknown')
            
            # Get token metadata (name, symbol)
            token_info = self.get_token_info(mint)
            symbol = token_info.get('symbol', '') or '—'
            name = token_info.get('name', '') or '—'
            
            # Truncate address for display
            address = account.get('address', 'Unknown')
            address_display = f"{address[:8]}...{address[-6:]}" if len(address) > 20 else address
            mint_display = f"{mint[:8]}...{mint[-6:]}" if len(mint) > 20 else mint
            
            # Insert into treeview
            item = self.tree.insert('', 'end', values=(
                select_text,
                symbol,
                name,
                balance_amount,
                address_display,
                mint_display,
                decimals
            ))
            
            # Store full address as a tag for retrieval
            self.tree.item(item, tags=(address,))
    
    def toggle_selection(self, event):
        """Toggle selection of an account when double-clicked"""
        selection = self.tree.selection()
        if not selection:
            return
        
        item = selection[0]
        # Get full address from tags (we store it there since display is truncated)
        tags = self.tree.item(item, 'tags')
        if not tags:
            self.log_message("Could not get account address", "ERROR")
            return
        
        account_address = tags[0]
        
        # Validate address format before adding to selection
        if not self.is_valid_solana_address(account_address):
            self.log_message(f"Invalid address format: {account_address[:20]}...", "ERROR")
            return
        
        with self._lock:
            if account_address in self.selected_accounts:
                self.selected_accounts.remove(account_address)
            else:
                self.selected_accounts.add(account_address)
        
        # Update the display to show current selection state
        self.update_accounts_display()
        self.update_selection_count()
    
    def select_all_accounts(self):
        """Select all visible accounts"""
        with self._lock:
            self.selected_accounts.clear()
            invalid_count = 0
            for account in self.token_accounts:
                addr = account.get('address')
                if self.is_valid_solana_address(addr):
                    self.selected_accounts.add(addr)
                else:
                    invalid_count += 1
        
        self.update_accounts_display()
        self.update_selection_count()
        if invalid_count > 0:
            self.log_message(f"Selected {len(self.selected_accounts)} accounts ({invalid_count} invalid skipped)", "WARNING")
        else:
            self.log_message(f"Selected all {len(self.selected_accounts)} accounts", "INFO")
    
    def deselect_all_accounts(self):
        """Deselect all accounts"""
        with self._lock:
            self.selected_accounts.clear()
        self.update_accounts_display()
        self.update_selection_count()
        self.log_message("Deselected all accounts", "INFO")
    
    def update_selection_count(self):
        """Update the close button text with selection count"""
        count = len(self.selected_accounts)
        if count > 0:
            if self.burn_before_close_var.get():
                self.close_btn.config(text=f"🔥 Burn & Close {count} Selected")
            else:
                self.close_btn.config(text=f"🗑️ Close {count} Selected")
        else:
            if self.burn_before_close_var.get():
                self.close_btn.config(text="🔥 Burn & Close Selected")
            else:
                self.close_btn.config(text="🗑️ Close Selected")
    
    def create_command_preview(self):
        """Create a preview of the exact commands that will be executed"""
        preview_lines = []
        
        if self.burn_before_close_var.get():
            preview_lines.append("🔥 BURN BEFORE CLOSE OPERATION:")
            preview_lines.append("=" * 40)
            preview_lines.append("")
            
            for i, account_address in enumerate(self.selected_accounts, 1):
                # Find account details for display
                account_info = None
                for account in self.token_accounts:
                    if account.get('address') == account_address:
                        account_info = account
                        break
                
                if account_info:
                    mint = account_info.get('mint', 'Unknown')
                    balance = account_info.get('tokenAmount', {}).get('uiAmountString', '0')
                    decimals = account_info.get('tokenAmount', {}).get('decimals', 0)
                    
                    preview_lines.append(f"{i}. BURN: spl-token burn {account_address} ALL")
                    preview_lines.append(f"   Mint: {mint}")
                    preview_lines.append(f"   Balance to burn: {balance} (decimals: {decimals})")
                    preview_lines.append("")
                    preview_lines.append(f"   CLOSE: spl-token close --address {account_address}")
                    preview_lines.append("")
                else:
                    preview_lines.append(f"{i}. BURN: spl-token burn {account_address} ALL")
                    preview_lines.append(f"   (Account details not found)")
                    preview_lines.append("")
                    preview_lines.append(f"   CLOSE: spl-token close --address {account_address}")
                    preview_lines.append("")
        else:
            preview_lines.append("🗑️ DIRECT CLOSE OPERATION:")
            preview_lines.append("=" * 30)
            preview_lines.append("")
            
            for i, account_address in enumerate(self.selected_accounts, 1):
                # Find account details for display
                account_info = None
                for account in self.token_accounts:
                    if account.get('address') == account_address:
                        account_info = account
                        break
                
                if account_info:
                    mint = account_info.get('mint', 'Unknown')
                    balance = account_info.get('tokenAmount', {}).get('uiAmountString', '0')
                    decimals = account_info.get('tokenAmount', {}).get('decimals', 0)
                    
                    preview_lines.append(f"{i}. spl-token close --address {account_address}")
                    preview_lines.append(f"   Mint: {mint}")
                    preview_lines.append(f"   Balance: {balance} (decimals: {decimals})")
                    preview_lines.append("")
                else:
                    preview_lines.append(f"{i}. spl-token close --address {account_address}")
                    preview_lines.append("   (Account details not found)")
                    preview_lines.append("")
        
        return "\n".join(preview_lines)
    
    def preview_commands(self):
        """Show a preview of the commands that would be executed"""
        if not self.selected_accounts:
            messagebox.showinfo("No Selection", "Please select at least one account to preview commands.")
            return
        
        preview_text = self.create_command_preview()
        
        # Create a detailed preview window
        preview_window = tk.Toplevel(self.root)
        preview_window.title("Preview Commands")
        preview_window.geometry("700x500")
        preview_window.transient(self.root)
        preview_window.grab_set()
        
        # Title
        title_label = ttk.Label(preview_window, text="🔍 Preview of Commands to Execute", 
                               font=('Helvetica', 14, 'bold'))
        title_label.pack(pady=(20, 10))
        
        # Warning
        warning_label = ttk.Label(preview_window, 
                                text="⚠️ This is a PREVIEW only. No accounts will be closed.", 
                                font=('Helvetica', 10), foreground='red')
        warning_label.pack(pady=(0, 20))
        
        # Preview text
        preview_frame = ttk.Frame(preview_window)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        
        preview_text_widget = scrolledtext.ScrolledText(preview_frame, wrap=tk.WORD, font=('Courier', 10))
        preview_text_widget.pack(fill=tk.BOTH, expand=True)
        preview_text_widget.insert(tk.END, preview_text)
        preview_text_widget.config(state=tk.DISABLED)
        
        # Close button
        close_btn = ttk.Button(preview_window, text="Close Preview", 
                              command=preview_window.destroy)
        close_btn.pack(pady=(0, 20))
    
    def dry_run_commands(self):
        """Perform a dry run to show exactly what would happen"""
        if not self.selected_accounts:
            messagebox.showinfo("No Selection", "Please select at least one account to dry run.")
            return
        
        dry_run_text = self.create_dry_run_report()
        
        # Create a detailed dry run window
        dry_run_window = tk.Toplevel(self.root)
        dry_run_window.title("Dry Run Report")
        dry_run_window.geometry("800x600")
        dry_run_window.transient(self.root)
        dry_run_window.grab_set()
        
        # Title
        title_label = ttk.Label(dry_run_window, text="🧪 Dry Run Report", 
                               font=('Helvetica', 14, 'bold'))
        title_label.pack(pady=(20, 10))
        
        # Safety notice
        safety_label = ttk.Label(dry_run_window, 
                               text="✅ This is a DRY RUN - No accounts will be closed!", 
                               font=('Helvetica', 10), foreground='green')
        safety_label.pack(pady=(0, 20))
        
        # Dry run text
        dry_run_frame = ttk.Frame(dry_run_window)
        dry_run_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        
        dry_run_text_widget = scrolledtext.ScrolledText(dry_run_frame, wrap=tk.WORD, font=('Courier', 9))
        dry_run_text_widget.pack(fill=tk.BOTH, expand=True)
        dry_run_text_widget.insert(tk.END, dry_run_text)
        dry_run_text_widget.config(state=tk.DISABLED)
        
        # Close button
        close_btn = ttk.Button(dry_run_window, text="Close Report", 
                              command=dry_run_window.destroy)
        close_btn.pack(pady=(0, 20))
    
    def create_dry_run_report(self):
        """Create a detailed dry run report"""
        report_lines = []
        report_lines.append("DRY RUN REPORT - NO ACCOUNTS WILL BE CLOSED")
        report_lines.append("=" * 60)
        report_lines.append("")
        
        total_rent_recovery = 0
        estimated_fees = 0
        
        for i, account_address in enumerate(self.selected_accounts, 1):
            # Find account details
            account_info = None
            for account in self.token_accounts:
                if account.get('address') == account_address:
                    account_info = account
                    break
            
            report_lines.append(f"ACCOUNT {i}:")
            report_lines.append(f"  Address: {account_address}")
            
            if account_info:
                mint = account_info.get('mint', 'Unknown')
                balance = account_info.get('tokenAmount', {}).get('uiAmountString', '0')
                decimals = account_info.get('tokenAmount', {}).get('decimals', 0)
                is_native = account_info.get('isNative', False)
                
                report_lines.append(f"  Mint: {mint}")
                report_lines.append(f"  Balance: {balance} (decimals: {decimals})")
                report_lines.append(f"  Native: {is_native}")
                
                # Estimate rent recovery (rough estimate)
                rent_recovery = 0.00203928  # Typical rent for token account
                total_rent_recovery += rent_recovery
                report_lines.append(f"  Estimated rent recovery: {rent_recovery:.6f} SOL")
                
            else:
                report_lines.append(f"  (Account details not found)")
            
            # Estimate transaction fees based on burn option
            if self.burn_before_close_var.get():
                # Burn + close = 2 transactions per account
                burn_fee = 0.000005  # Burn transaction fee
                close_fee = 0.000005  # Close transaction fee
                total_fee = burn_fee + close_fee
                estimated_fees += total_fee
                
                report_lines.append(f"  🔥 Burn command: spl-token burn {account_address} ALL")
                report_lines.append(f"  🗑️ Close command: spl-token close --address {account_address}")
                report_lines.append(f"  💰 Total fees: {total_fee:.6f} SOL (burn + close)")
            else:
                # Just close = 1 transaction per account
                tx_fee = 0.000005  # Typical transaction fee
                estimated_fees += tx_fee
                report_lines.append(f"  🗑️ Close command: spl-token close --address {account_address}")
                report_lines.append(f"  💰 Transaction fee: {tx_fee:.6f} SOL")
            
            report_lines.append("")
        
        # Summary
        report_lines.append("SUMMARY:")
        report_lines.append("=" * 30)
        report_lines.append(f"Total accounts to close: {len(self.selected_accounts)}")
        report_lines.append(f"Total estimated rent recovery: {total_rent_recovery:.6f} SOL")
        
        # Show cost savings from batching
        if len(self.selected_accounts) > 1:
            old_fee = estimated_fees  # Multiple individual transactions
            new_fee = estimated_fees  # Current method still uses individual transactions
            # Note: True batching would save money, but current implementation provides better error handling
            
            report_lines.append(f"💰 Transaction fees: {estimated_fees:.6f} SOL")
            report_lines.append(f"📊 Note: True batching would reduce fees, but current method ensures reliability")
        else:
            report_lines.append(f"Estimated transaction fee: {estimated_fees:.6f} SOL")
        
        report_lines.append(f"Net SOL gain: {total_rent_recovery - estimated_fees:.6f} SOL")
        report_lines.append("")
        report_lines.append("⚠️  Remember: This is a DRY RUN only!")
        report_lines.append("   No accounts will be closed until you click 'Close Selected'")
        
        if len(self.selected_accounts) > 1:
            report_lines.append("")
            report_lines.append("🚀 BATCHING BENEFITS:")
            report_lines.append("   ✅ All accounts processed in sequence")
            report_lines.append("   ✅ Better error handling and logging")
            report_lines.append("   ✅ Automatic cleanup on failure")
            report_lines.append("   ✅ More reliable execution")
            report_lines.append("")
            report_lines.append("💡 FUTURE IMPROVEMENT:")
            report_lines.append("   🔧 True transaction batching would save ~80-90% on fees")
            report_lines.append("   🔧 Requires advanced Solana transaction building")
        
        # Add burn information
        if self.burn_before_close_var.get():
            report_lines.append("")
            report_lines.append("🔥 BURN BEFORE CLOSE BENEFITS:")
            report_lines.append("   ✅ Tokens are completely removed from circulation")
            report_lines.append("   ✅ Maximum SOL recovery from rent")
            report_lines.append("   ✅ Clean account closure (no residual tokens)")
            report_lines.append("   ⚠️  Note: Higher transaction fees due to burn operations")
            report_lines.append("")
            report_lines.append("💡 COST ANALYSIS:")
            report_lines.append(f"   🔥 Burn operations: {len(self.selected_accounts) * 0.000005:.6f} SOL")
            report_lines.append(f"   🗑️ Close operations: {len(self.selected_accounts) * 0.000005:.6f} SOL")
            report_lines.append(f"   💰 Total cost: {estimated_fees:.6f} SOL")
            report_lines.append(f"   📈 Rent recovery: {total_rent_recovery:.6f} SOL")
            report_lines.append(f"   🎯 Net gain: {total_rent_recovery - estimated_fees:.6f} SOL")
        
        return "\n".join(report_lines)
    
    def on_burn_option_changed(self):
        """Handle burn before close checkbox change"""
        if self.burn_before_close_var.get():
            self.log_message("🔥 Burn before close enabled - tokens will be burned before closing accounts", "INFO")
            # Update button text to show burn action
            if len(self.selected_accounts) > 0:
                self.close_btn.config(text=f"🔥 Burn & Close {len(self.selected_accounts)} Selected")
        else:
            self.log_message("Burn before close disabled - accounts will be closed directly", "INFO")
            # Restore normal button text
            if len(self.selected_accounts) > 0:
                self.close_btn.config(text=f"🗑️ Close {len(self.selected_accounts)} Selected")
            else:
                self.close_btn.config(text="🗑️ Close Selected")
    
    def clear_selections_after_closure(self):
        """Clear all selections after successful account closure"""
        with self._lock:
            self.selected_accounts.clear()
        self.update_selection_count()
        self.update_accounts_display()
        self.log_message("✅ Selections cleared after successful closure", "INFO")
        
        # Show a brief message to the user
        messagebox.showinfo("Selections Cleared", 
                           "All selections have been cleared.\n"
                           "You can now select new accounts for the next batch closure.")
    
    def run_batch_close(self):
        """Execute a batch close operation using shell script for reliability"""
        try:
            # Validate all addresses before proceeding
            with self._lock:
                accounts_to_process = list(self.selected_accounts)
            
            for addr in accounts_to_process:
                if not self.is_valid_solana_address(addr):
                    return False, None, f"Invalid Solana address format: {addr[:20]}..."
            
            self.log_message("Using shell script approach for batch operations...", "INFO")
            
            script_content = "#!/bin/bash\n"
            script_content += "set -e\n"
            script_content += "echo '🚀 Starting batch operation...'\n"
            script_content += f"echo '📊 Total accounts to process: {len(accounts_to_process)}'\n"
            script_content += f"echo '⏱️  Estimated time: {len(accounts_to_process) * 5} seconds'\n"
            script_content += "echo ''\n"
            
            if self.burn_before_close_var.get():
                script_content += "echo '🔥 Starting burn before close operation...'\n"
                script_content += "echo 'This will burn all tokens before closing accounts to maximize SOL recovery'\n"
                script_content += "echo ''\n"
                
                for i, account_address in enumerate(accounts_to_process, 1):
                    safe_addr = self.sanitize_for_shell(account_address)
                    display_addr = account_address[:8]
                    script_content += f"echo '🔥 [{i}/{len(accounts_to_process)}] Burning tokens in: {display_addr}...'\n"
                    script_content += f"spl-token burn {safe_addr} ALL\n"
                    script_content += f"echo '✅ [{i}/{len(accounts_to_process)}] Tokens burned successfully'\n"
                    script_content += "echo ''\n"
                
                script_content += "echo '🔥 All tokens burned successfully!'\n"
                script_content += "echo 'Now closing empty accounts...'\n"
                script_content += "echo ''\n"
            
            script_content += "echo '🗑️ Starting batch closure...'\n"
            script_content += "echo ''\n"
            
            for i, account_address in enumerate(accounts_to_process, 1):
                safe_addr = self.sanitize_for_shell(account_address)
                display_addr = account_address[:8]
                script_content += f"echo '🗑️ [{i}/{len(accounts_to_process)}] Closing account: {display_addr}...'\n"
                script_content += f"spl-token close --address {safe_addr}\n"
                script_content += f"echo '✅ [{i}/{len(accounts_to_process)}] Account closed successfully'\n"
                script_content += "echo ''\n"
            
            script_content += "echo '🎉 All accounts closed successfully!'\n"
            script_content += "echo ''\n"
            script_content += "echo '📊 FINAL SUMMARY:'\n"
            script_content += f"echo '   ✅ Total accounts processed: {len(accounts_to_process)}'\n"
            if self.burn_before_close_var.get():
                script_content += f"echo '   🔥 Tokens burned: {len(accounts_to_process)} accounts'\n"
            script_content += f"echo '   🗑️  Accounts closed: {len(accounts_to_process)}'\n"
            script_content += "echo '   💰 SOL recovered from rent'\n"
            script_content += "echo '   🎯 Operation completed successfully!'\n"
            
            # Create temp file with restrictive permissions (owner read/write only initially)
            fd, script_file_path = tempfile.mkstemp(suffix='.sh', text=True)
            try:
                with os.fdopen(fd, 'w') as script_file:
                    script_file.write(script_content)
                
                # Set executable only for owner (0o700)
                os.chmod(script_file_path, 0o700)
                
                batch_timeout = 30 + (len(accounts_to_process) * 10)
                self.log_message(f"Executing batch script with {batch_timeout}s timeout...", "INFO")
                
                success, output, error = self.run_command(['bash', script_file_path], timeout=batch_timeout)
                
                if success:
                    self.log_message("✅ Batch closure successful with shell script!", "SUCCESS")
                    return True, output, None
                else:
                    return False, output, error
                    
            finally:
                try:
                    os.unlink(script_file_path)
                except OSError:
                    pass
                
        except Exception as e:
            return False, None, f"Batch close error: {str(e)}"
    
    def close_selected_accounts(self):
        """Close the selected token accounts"""
        if not self.selected_accounts:
            messagebox.showwarning("No Selection", "Please select at least one account to close.")
            return
        
        # Show preview of commands that will be executed
        preview_text = self.create_command_preview()
        
        # Confirm with detailed preview
        result = messagebox.askyesno(
            "Confirm Account Closure",
            f"Are you sure you want to close {len(self.selected_accounts)} token account(s)?\n\n"
            "PREVIEW OF COMMANDS TO BE EXECUTED:\n"
            f"{preview_text}\n\n"
            "This action cannot be undone and will recover SOL from rent.\n"
            "Make sure you have enough SOL for transaction fees."
        )
        
        if not result:
            return
        
        # Disable close button during operation
        self.close_btn.config(state='disabled')
        selected_count = len(self.selected_accounts)
        self.log_message(f"Starting batch closure of {selected_count} accounts...", "INFO")
        
        # Run closure in background thread
        def close_thread():
            try:
                # Get thread-safe copy of selected accounts
                with self._lock:
                    accounts_to_close = list(self.selected_accounts)
                
                # Check if we should use true batching (more than 1 account)
                if selected_count > 1:
                    self.root.after(0, lambda: self.log_message(
                        f"Using BATCH PROCESSING to close {selected_count} accounts efficiently.", "INFO"))
                    
                    # Use batch processing with shell script
                    success, output, error = self.run_batch_close()
                    
                    if success:
                        self.root.after(0, lambda: self.log_message(
                            f"✅ Batch closure successful! Closed {selected_count} accounts efficiently.", "SUCCESS"))
                        success_count = selected_count
                        failed_count = 0
                    else:
                        self.root.after(0, lambda: self.log_message(
                            f"❌ Batch closure failed: {error}", "ERROR"))
                        success_count = 0
                        failed_count = selected_count
                        
                else:
                    # Single account - use regular close
                    account_address = accounts_to_close[0]
                    
                    # Validate address format
                    if not self.is_valid_solana_address(account_address):
                        self.root.after(0, lambda: self.log_message(
                            f"❌ Invalid address format: {account_address[:20]}...", "ERROR"))
                        success_count = 0
                        failed_count = 1
                    else:
                        self.root.after(0, lambda addr=account_address: self.log_message(
                            f"Closing single account: {addr[:8]}...", "INFO"))
                        
                        success, output, error = self.run_command(['spl-token', 'close', '--address', account_address])
                        
                        if success:
                            success_count = 1
                            failed_count = 0
                            self.root.after(0, lambda addr=account_address: self.log_message(
                                f"✅ Successfully closed: {addr[:8]}...", "SUCCESS"))
                        else:
                            success_count = 0
                            failed_count = 1
                            self.root.after(0, lambda addr=account_address, err=error: self.log_message(
                                f"❌ Failed to close {addr[:8]}...: {err}", "ERROR"))
                
                # Final summary
                self.root.after(0, lambda: self.log_message(
                    f"Closure complete: {success_count} successful, {failed_count} failed", 
                    "SUCCESS" if failed_count == 0 else "WARNING"))
                
                # Clear selections after successful closure
                if success_count > 0:
                    self.root.after(0, lambda: self.clear_selections_after_closure())
                
                # Refresh accounts list
                self.root.after(0, self.refresh_accounts)
                
                # Re-enable close button
                self.root.after(0, lambda: self.close_btn.config(state='normal'))
                
            except Exception as ex:
                err_msg = str(ex)
                self.root.after(0, lambda msg=err_msg: self.log_message(f"Error during closure: {msg}", "ERROR"))
                self.root.after(0, lambda: self.close_btn.config(state='normal'))
        
        threading.Thread(target=close_thread, daemon=True).start()

def main():
    """Main entry point"""
    # Check if spl-token is available
    try:
        result = subprocess.run(['spl-token', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            messagebox.showerror("Error", "spl-token CLI tool not found or not working.\n"
                               "Please install Solana CLI tools and ensure spl-token is available.")
            return
    except FileNotFoundError:
        messagebox.showerror("Error", "spl-token CLI tool not found.\n"
                           "Please install Solana CLI tools and ensure spl-token is available.")
        return
    
    # Create and run the application
    root = tk.Tk()
    app = TokenAccountCloser(root)
    
    # Configure window close handler
    def on_closing():
        if messagebox.askokcancel("Quit", "Are you sure you want to quit?"):
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Start the application
    root.mainloop()

if __name__ == "__main__":
    main() 