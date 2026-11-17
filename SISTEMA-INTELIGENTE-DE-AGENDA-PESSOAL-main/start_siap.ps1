$ErrorActionPreference = 'Stop'

$mysqlHome = 'C:\Program Files\MySQL\MySQL Server 8.4'
$mysqld = Join-Path $mysqlHome 'bin\mysqld.exe'
$mysql = Join-Path $mysqlHome 'bin\mysql.exe'
$configDirectory = 'C:\ProgramData\MySQL\SIAP'
$configFile = Join-Path $configDirectory 'my.ini'
$adminFile = Join-Path $env:USERPROFILE 'AppData\Local\SIAP\mysql-admin.cnf'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$appServer = Join-Path $PSScriptRoot 'server.py'

foreach ($requiredPath in @($mysqld, $mysql, $configFile, $adminFile, $python, $appServer, (Join-Path $PSScriptRoot '.env'))) {
    if (-not (Test-Path $requiredPath)) {
        throw "Arquivo necessário não encontrado: $requiredPath"
    }
}

function Test-MySqlConnection {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $mysql "--defaults-extra-file=$adminFile" '--execute=SELECT 1' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

if (-not (Test-MySqlConnection)) {
    Write-Host 'Iniciando o banco MySQL local...'
    $stdout = Join-Path $configDirectory 'mysql-server.stdout.log'
    $stderr = Join-Path $configDirectory 'mysql-server.stderr.log'
    Start-Process -FilePath $mysqld -ArgumentList @("--defaults-file=`"$configFile`"", '--console') -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $ready = $false
    do {
        Start-Sleep -Milliseconds 500
        $ready = Test-MySqlConnection
    } until ($ready -or [DateTime]::UtcNow -ge $deadline)
    if (-not $ready) {
        throw 'O MySQL não respondeu. Consulte C:\ProgramData\MySQL\SIAP\mysql-error.log.'
    }
}

Write-Host 'CALÊ iniciado em http://localhost:8000'
Write-Host 'Mantenha esta janela aberta enquanto usar o app. Pressione Ctrl+C para encerrar a API.'
& $python $appServer
