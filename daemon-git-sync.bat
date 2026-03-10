@echo off
cd /d "%~dp0"

:: Try uv first, then fall back to plain python
where uv >nul 2>&1
if %errorlevel% == 0 (
    uv run cli.py run %*
) else (
    python cli.py run %*
)
