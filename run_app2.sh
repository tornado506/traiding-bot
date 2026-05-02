#!/bin/bash
cd ~/Documents/bot_project
source venv_mac/bin/activate
streamlit run app2.py --server.port 8502 --server.address localhost
