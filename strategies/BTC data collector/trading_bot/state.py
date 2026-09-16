# state.py
import json
import os
import tempfile


def load_state(filepath: str, is_btc: bool = True) -> dict:
    """Load state from JSON. If file doesn't exist, return default state."""
    if not os.path.exists(filepath):
        return {
            "balance":       0.0,   # Tracked from MT5 account equity; 0.0 = not yet initialized
            "open_trade":    None,
            "trade_history": [],
            "last_run":      None,
            "total_trades":  0,
            "total_pnl":     0.0,
            "pending_order": False,
        }
    
    try:
        with open(filepath, 'r') as f:
            state = json.load(f)
            # Ensure pending_order exists
            if "pending_order" not in state:
                state["pending_order"] = False
            return state
    except json.JSONDecodeError:
        raise ValueError(f"State file {filepath} is corrupted. Halting to prevent unsafe trades.")

def save_state(filepath: str, state: dict) -> None:
    """Write state to JSON atomically (write to .tmp, then rename)."""
    dirname = os.path.dirname(filepath)
    if dirname and not os.path.exists(dirname):
        os.makedirs(dirname)
        
    fd, temp_path = tempfile.mkstemp(dir=dirname, text=True)
    with os.fdopen(fd, 'w') as f:
        json.dump(state, f, indent=4)
        
    # Atomic replace
    os.replace(temp_path, filepath)
