param([switch]$Dev)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$appPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $appPython)) { $appPython = Join-Path $PSScriptRoot 'md\.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $appPython)) { throw '未找到 Python 环境。请先按 README.md 安装依赖，或运行 setup.ps1。' }
if ($Dev) {
  Write-Host '后端 http://127.0.0.1:8765。另开终端执行 cd frontend; npm run dev。'
} elseif (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'frontend\dist\index.html'))) {
  throw '请先执行 npm --prefix frontend install 和 npm --prefix frontend run build，或运行 setup.ps1。'
}
Write-Host '观微本地工作台：http://127.0.0.1:8765（Ctrl+C 停止）'
& $appPython -m uvicorn insight.app:app --app-dir backend --host 127.0.0.1 --port 8765
