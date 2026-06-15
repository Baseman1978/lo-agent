@echo off
REM ============================================================
REM  Span - autoriseer voor je Claude-abonnement (Agent SDK)
REM  Levert een CLAUDE_CODE_OAUTH_TOKEN (1 jaar geldig) voor de
REM  SDK-transitie + het spike-script (scripts\spike_sdk.py).
REM ============================================================
setlocal

echo.
echo [1/3] Controleer Claude Code CLI...
where claude >nul 2>nul
if errorlevel 1 (
  echo     Claude Code CLI niet gevonden.
  echo     Installeer eerst Node.js, daarna:  npm install -g @anthropic-ai/claude-code
  echo.
  pause
  exit /b 1
)

echo [2/3] Let op: ANTHROPIC_API_KEY mag NIET in je omgeving staan
echo       (een API-key wint altijd van het abonnement).
if defined ANTHROPIC_API_KEY (
  echo     LET OP: ANTHROPIC_API_KEY is nu gezet in deze sessie.
  echo     Open een schone terminal zonder die variabele, of unset 'm.
  echo.
)

echo [3/3] Start de abonnement-login (browser opent)...
echo.
claude setup-token

echo.
echo ============================================================
echo  Klaar. Kopieer het token hierboven en zet het in je .env:
echo      CLAUDE_CODE_OAUTH_TOKEN=^<token^>
echo  Daarna kun je draaien:
echo      pip install claude-agent-sdk
echo      python scripts\spike_sdk.py
echo ============================================================
echo.
pause
endlocal
