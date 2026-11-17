@echo off
REM Inicia o ShadowIADetector (agente local + dashboard)
cd /d "%~dp0"
python -m shadowia_detector run --open %*
