@echo off
title ETHUSDT Dashboard
echo Starting Streamlit Dashboard...
python -m pip install streamlit plotly
python -m streamlit run dashboard.py
pause
