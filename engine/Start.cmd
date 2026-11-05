@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "local\start-dev.cmd" %*
