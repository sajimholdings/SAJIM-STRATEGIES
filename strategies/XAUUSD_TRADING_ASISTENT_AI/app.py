import streamlit as st
import json
import time
from datetime import datetime
import os

st.set_page_config(page_title="XAUUSD AI Scalper", layout="wide")

st.title("🤖 XAUUSD AI Volume Scalper")

def load_state():
    if os.path.exists("shared_state.json"):
        try:
            with open("shared_state.json", "r") as f:
                return json.load(f)
        except:
            return None
    return None

st.markdown("""
<style>
.stApp {background-color: #0e1117;}
.metric-box {
    background-color: #1f2937;
    padding: 20px;
    border-radius: 10px;
    border: 1px solid #374151;
}
</style>
""", unsafe_allow_html=True)

state = load_state()

col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("### 🧠 AI Neural Core")
    if state and "ai" in state:
        ai = state["ai"]
        st.info(f"**Current Thought:** {ai.get('thought', '...')}")
        
        c1, c2 = st.columns(2)
        signal_val = ai.get("signal", "N/A")
        if isinstance(signal_val, dict):
            signal_val = signal_val.get("action", str(signal_val))
            
        c1.metric("Signal", str(signal_val))
        c2.metric("Confidence", f"{ai.get('confidence', 0.0)*100:.1f}%")
        
        st.caption(f"Last updated: {state.get('timestamp', 'N/A')}")
    else:
        st.warning("Waiting for bot to initialize...")

with col2:
    st.markdown("### ⚙️ Control Panel")
    st.info("The bot runs completely autonomously in the background (`bot.py`).")
    if st.button("Refresh Dashboard"):
        st.rerun()

time.sleep(2)
st.rerun()
