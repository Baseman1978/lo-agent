@echo off
title Span Web - een AI die zichzelf onthoudt
cd /d "%~dp0"
chcp 65001 >nul

rem -- Docker engine check; zo nodig Docker Desktop starten --
docker info >nul 2>&1
if not errorlevel 1 goto docker_ok
echo Docker Desktop starten...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a tries=0
:wait_docker
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ok
set /a tries+=1
if %tries% lss 30 goto wait_docker
echo Docker komt niet omhoog. Start Docker Desktop handmatig en probeer opnieuw.
pause
exit /b 1
:docker_ok

rem -- oude losse neo4j-container mag niet samen met de stack draaien --
docker stop span-neo4j >nul 2>&1

rem -- volledige stack bouwen en starten --
echo Span-stack starten (eerste keer duurt het bouwen even)...
docker compose up -d --build
if errorlevel 1 (
  echo Stack starten mislukt. Check: docker compose logs
  pause
  exit /b 1
)

echo Wachten tot de web-UI reageert...
set /a tries=0
:wait_web
powershell -NoProfile -Command "try{ $r=Invoke-WebRequest -Uri http://localhost:8472 -UseBasicParsing -TimeoutSec 2; exit 0 }catch{ exit 1 }" >nul 2>&1
if not errorlevel 1 goto web_ok
timeout /t 2 /nobreak >nul
set /a tries+=1
if %tries% lss 60 goto wait_web
echo Web-UI komt niet omhoog. Check: docker compose logs span
pause
exit /b 1
:web_ok

rem -- token uit .env meesturen zodat de browser direct ingelogd is --
set SPAN_TOKEN=
for /f "tokens=2 delims==" %%a in ('findstr /b "SPAN_AUTH_TOKEN=" .env') do set SPAN_TOKEN=%%a
if defined SPAN_TOKEN (
  start "" "http://localhost:8472/?token=%SPAN_TOKEN%"
) else (
  start "" "http://localhost:8472"
)
echo Span draait op http://localhost:8472 - dit venster mag dicht.
timeout /t 5 >nul
