@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
if not exist "packages\standalone\dist\index.html" (
  echo 首次运行，正在构建前端，请稍候...
  call pnpm build "standalone..."
  if errorlevel 1 (
    echo 构建失败，请检查上面的报错。
    pause
    exit /b 1
  )
)
node local\server.mjs %*
pause
