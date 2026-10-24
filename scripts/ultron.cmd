@echo off
rem ULTRON one-command launcher. Double-click this file or run:
rem   scripts\ultron.cmd            start everything
rem   scripts\ultron.cmd -Stop      stop Electron, API and PostgreSQL
rem   scripts\ultron.cmd -InstallShortcut   put ULTRON.lnk on the desktop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ultron.ps1" %*
if errorlevel 1 pause
