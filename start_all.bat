@echo off
rem Starts the monitoring agent (which also starts the dashboard and opens the browser) and the insert-record window.
cd /d "%~dp0"
start "Monitor Agent" .venv\Scripts\python.exe monitor_agent.py
start "" .venv\Scripts\pythonw.exe insert_record_gui.py
