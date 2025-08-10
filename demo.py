#!/usr/bin/env python3
"""
Demo version of the Token Account Closer
This version shows the UI without requiring actual Solana CLI tools
Useful for testing the interface before setting up the full environment
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import json
import threading
from typing import List, Dict, Optional
import os

class TokenAccountCloserDemo:
    def __init__(self, root):
        self.root = root
        self.root.title("Solana Token Account Closer - DEMO MODE")
        self.root.geometry("900x700")
        self.root.configure(bg='#f0f0f0')
        
        # Demo data storage
        self.token_accounts = []
        self.selected_accounts = set()
        
        # Create UI
        self.create_widgets()
        
        # Load demo accounts
        self.load_demo_accounts()
    
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
        
        # Title with DEMO indicator
        title_label = ttk.Label(main_frame, text="🔒 Solana Token Account Closer - DEMO MODE", 
                               font=('Helvetica', 16, 'bold'), foreground='orange')
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))
        
        # Demo notice
        notice_label = ttk.Label(main_frame, text="⚠️ This is a demo version. No actual accounts will be closed.", 
                                font=('Helvetica', 10), foreground='red')
        notice_label.grid(row=1, column=0, columnspan=3, pady=(0, 20))
        
        # Control buttons frame
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=2, column=0, columnspan=3, pady=(0, 10), sticky=(tk.W, tk.E))
        
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
        
        # Burn before close checkbox
        self.burn_before_close_var = tk.BooleanVar()
        self.burn_checkbox = ttk.Checkbutton(control_frame, text="🔥 Burn Before Close", 
                                           variable=self.burn_before_close_var,
                                           command=self.on_burn_option_changed)
        self.burn_checkbox.pack(side=tk.LEFT, padx=(0, 10))
        
        # Close selected button
        self.close_btn = ttk.Button(control_frame, text="🗑️ Close Selected (DEMO)", 
                                   command=self.close_selected_accounts, 
                                   style='Danger.TButton')
        self.close_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Status label
        self.status_label = ttk.Label(control_frame, text="DEMO MODE - Ready", foreground='orange')
        self.status_label.pack(side=tk.RIGHT)
        
        # Accounts list frame
        list_frame = ttk.LabelFrame(main_frame, text="Token Accounts (Demo Data)", padding="5")
        list_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        
        # Create Treeview for accounts
        columns = ('select', 'address', 'mint', 'balance', 'decimals', 'owner')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=15)
        
        # Define headings
        self.tree.heading('select', text='Select')
        self.tree.heading('address', text='Account Address')
        self.tree.heading('mint', text='Token Mint')
        self.tree.heading('balance', text='Balance')
        self.tree.heading('decimals', text='Decimals')
        self.tree.heading('owner', text='Owner')
        
        # Configure column widths
        self.tree.column('select', width=60, anchor='center')
        self.tree.column('address', width=200)
        self.tree.column('mint', width=200)
        self.tree.column('balance', width=100, anchor='e')
        self.tree.column('decimals', width=80, anchor='center')
        self.tree.column('owner', width=200)
        
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
        log_frame = ttk.LabelFrame(main_frame, text="Operation Log (Demo)", padding="5")
        log_frame.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        # Log text area
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure row weights for main frame
        main_frame.rowconfigure(3, weight=3)
        main_frame.rowconfigure(4, weight=1)
    
    def load_demo_accounts(self):
        """Load demo token account data"""
        self.token_accounts = [
            {
                "address": "DemoAccount111111111111111111111111111111111",
                "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "owner": "DemoOwner111111111111111111111111111111111",
                "tokenAmount": {
                    "uiAmountString": "1000.5",
                    "decimals": 6
                }
            },
            {
                "address": "DemoAccount222222222222222222222222222222222",
                "mint": "So11111111111111111111111111111111111111112",
                "owner": "DemoOwner111111111111111111111111111111111",
                "tokenAmount": {
                    "uiAmountString": "250.75",
                    "decimals": 9
                }
            },
            {
                "address": "DemoAccount333333333333333333333333333333333",
                "mint": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
                "owner": "DemoOwner111111111111111111111111111111111",
                "tokenAmount": {
                    "uiAmountString": "0.001",
                    "decimals": 6
                }
            }
        ]
        self.update_accounts_display()
        self.log_message("Loaded demo token accounts", "INFO")
    
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
    
    def refresh_accounts(self):
        """Refresh the list of token accounts (demo version)"""
        self.log_message("Refreshing demo accounts...", "INFO")
        self.load_demo_accounts()
        self.log_message(f"Refreshed {len(self.token_accounts)} demo accounts", "SUCCESS")
    
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
            
            # Get owner
            owner = account.get('owner', 'Unknown')
            
            # Insert into treeview
            item = self.tree.insert('', 'end', values=(
                select_text,
                account.get('address', 'Unknown'),
                mint,
                balance_amount,
                decimals,
                owner
            ))
            
            # Store account data in item
            self.tree.set(item, 'select', select_text)
    
    def toggle_selection(self, event):
        """Toggle selection of an account when double-clicked"""
        item = self.tree.selection()[0]
        account_address = self.tree.item(item, 'values')[1]
        
        if account_address in self.selected_accounts:
            self.selected_accounts.remove(account_address)
            self.tree.set(item, 'select', '☐')
        else:
            self.selected_accounts.add(account_address)
            self.tree.set(item, 'select', '☑️')
        
        self.update_selection_count()
    
    def select_all_accounts(self):
        """Select all visible accounts"""
        self.selected_accounts.clear()
        for account in self.token_accounts:
            self.selected_accounts.add(account.get('pubkey'))
        
        self.update_accounts_display()
        self.update_selection_count()
        self.log_message(f"Selected all {len(self.selected_accounts)} demo accounts", "INFO")
    
    def deselect_all_accounts(self):
        """Deselect all accounts"""
        self.selected_accounts.clear()
        self.update_accounts_display()
        self.update_selection_count()
        self.log_message("Deselected all demo accounts", "INFO")
    
    def update_selection_count(self):
        """Update the close button text with selection count"""
        count = len(self.selected_accounts)
        if count > 0:
            self.close_btn.config(text=f"🗑️ Close {count} Selected (DEMO)")
        else:
            self.close_btn.config(text="🗑️ Close Selected (DEMO)")
    
    def close_selected_accounts(self):
        """Close the selected token accounts (demo version)"""
        if not self.selected_accounts:
            messagebox.showwarning("No Selection", "Please select at least one account to close.")
            return
        
        # Confirm deletion (demo version)
        count = len(self.selected_accounts)
        result = messagebox.askyesno(
            "DEMO: Confirm Account Closure",
            f"This is a DEMO version.\n\n"
            f"You selected {count} account(s) to close.\n"
            f"In the real application, this would close the accounts and recover SOL.\n\n"
            f"Continue with demo?"
        )
        
        if not result:
            return
        
        # Simulate closure process (demo)
        self.log_message(f"DEMO: Starting batch closure of {count} accounts...", "INFO")
        
        def demo_close_thread():
            try:
                for i, account_address in enumerate(self.selected_accounts, 1):
                    self.root.after(0, lambda addr=account_address, idx=i: 
                        self.log_message(f"DEMO: Closing account {idx}/{count}: {addr[:8]}...", "INFO"))
                    
                    # Simulate processing time
                    import time
                    time.sleep(0.5)
                    
                    self.root.after(0, lambda addr=account_address: 
                        self.log_message(f"DEMO: Successfully closed: {addr[:8]}...", "SUCCESS"))
                
                # Final summary
                self.root.after(0, lambda: self.log_message(
                    f"DEMO: Batch closure complete: {count} accounts processed", "SUCCESS"))
                
                # Clear selection
                self.root.after(0, self.deselect_all_accounts)
                
            except Exception as e:
                self.root.after(0, lambda: self.log_message(f"DEMO: Error during batch closure: {str(e)}", "ERROR"))
        
        threading.Thread(target=demo_close_thread, daemon=True).start()
    
    def on_burn_option_changed(self):
        """Handle burn before close checkbox change"""
        if self.burn_before_close_var.get():
            self.log_message("🔥 Burn before close enabled - tokens will be burned before closing accounts", "INFO")
            # Update button text to show burn action
            if len(self.selected_accounts) > 0:
                self.close_btn.config(text=f"🔥 Burn & Close {len(self.selected_accounts)} Selected (DEMO)")
        else:
            self.log_message("Burn before close disabled - accounts will be closed directly", "INFO")
            # Restore normal button text
            if len(self.selected_accounts) > 0:
                self.close_btn.config(text=f"🗑️ Close {len(self.selected_accounts)} Selected (DEMO)")
            else:
                self.close_btn.config(text="🗑️ Close Selected (DEMO)")

def main():
    """Main entry point for demo"""
    # Create and run the demo application
    root = tk.Tk()
    app = TokenAccountCloserDemo(root)
    
    # Configure window close handler
    def on_closing():
        if messagebox.askokcancel("Quit Demo", "Are you sure you want to quit the demo?"):
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Start the demo application
    root.mainloop()

if __name__ == "__main__":
    main() 