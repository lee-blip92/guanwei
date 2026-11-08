$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
  if (Test-Path -LiteralPath 'md\.venv\Scripts\python.exe') {
    & '.\md\.venv\Scripts\python.exe' -m venv .venv
  } else { py -3.11 -m venv .venv }
  if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw '后端依赖安装失败' }
& npm.cmd --prefix frontend install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
& npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
Write-Host '安装完成。复制 .env.example 为 .env 后可配置模型；双击 start.cmd 启动。'
