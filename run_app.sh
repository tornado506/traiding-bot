#!/bin/bash
cd ~/Documents/bot_project
source venv_mac/bin/activate
streamlit run app.py --server.port 8501 --server.address localhost
