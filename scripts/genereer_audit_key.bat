@echo off
REM Genereer een audit-HMAC-sleutel en zet 'm in .env.
REM Dubbelklik dit bestand of draai het vanuit een terminal.
python "%~dp0genereer_audit_key.py"
echo.
pause
