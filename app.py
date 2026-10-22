#!/usr/bin/env python3
"""
Root entry point for Streamlit Community Cloud, Hugging Face, Render, Docker, and local deployment.
Directly loads and executes dashboard/app.py.
"""
import os
import sys

# Ensure project root is in Python module search path
root_dir = os.path.dirname(os.path.abspath(__file__))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Execute the central security operations dashboard
dashboard_file = os.path.join(root_dir, "dashboard", "app.py")
with open(dashboard_file, "r", encoding="utf-8") as f:
    code = f.read()

exec(compile(code, dashboard_file, "exec"), globals())
