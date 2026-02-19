$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8000

$alreadyRunning = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($alreadyRunning) {
    exit 0
}

$py = (Get-Command py -ErrorAction Stop).Source
Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory $projectRoot -WindowStyle Hidden
