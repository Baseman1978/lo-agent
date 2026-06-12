@echo off
title Span - een AI die zichzelf onthoudt
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

rem -- Neo4j starten (compose-stack; oude losse container eerst stoppen) --
docker stop span-neo4j >nul 2>&1
docker compose up -d neo4j >nul 2>&1
echo Wachten op Neo4j...
set /a tries=0
:wait_neo4j
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{ $c.Connect('localhost',7687); exit 0 }catch{ exit 1 }finally{ $c.Close() }" >nul 2>&1
if not errorlevel 1 goto neo4j_ok
timeout /t 2 /nobreak >nul
set /a tries+=1
if %tries% lss 45 goto wait_neo4j
echo Neo4j komt niet omhoog. Check: docker logs span-neo4j
pause
exit /b 1
:neo4j_ok

rem -- Span chat --
".venv\Scripts\span.exe" chat
echo.
pause
