@echo off
cd /d "%~dp0tests"
py -3 -m unittest discover -p "test_*.py"
pause
