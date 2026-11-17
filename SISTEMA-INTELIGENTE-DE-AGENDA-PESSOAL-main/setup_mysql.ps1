$ErrorActionPreference = 'Stop'

$mysqlHome = 'C:\Program Files\MySQL\MySQL Server 8.4'
$mysqld = Join-Path $mysqlHome 'bin\mysqld.exe'
$mysql = Join-Path $mysqlHome 'bin\mysql.exe'
$configDirectory = 'C:\ProgramData\MySQL\SIAP'
$dataDirectory = Join-Path $configDirectory 'Data'
$configFile = Join-Path $configDirectory 'my.ini'
$serverStdout = Join-Path $configDirectory 'mysql-server.stdout.log'
$serverStderr = Join-Path $configDirectory 'mysql-server.stderr.log'
$adminDirectory = Join-Path $env:USERPROFILE 'AppData\Local\SIAP'
$adminFile = Join-Path $adminDirectory 'mysql-admin.cnf'
$envFile = Join-Path $PSScriptRoot '.env'
$schemaFile = Join-Path $PSScriptRoot 'schema.sql'

if (-not (Test-Path $mysqld) -or -not (Test-Path $mysql)) {
    throw 'MySQL Server 8.4 não foi encontrado no caminho padrão. Instale o MySQL Server antes de executar este arquivo.'
}
if (-not (Test-Path $schemaFile)) {
    throw 'O arquivo schema.sql não foi encontrado na pasta do CALÊ.'
}
if (Test-Path $envFile) {
    throw 'O arquivo .env já existe. Para evitar substituir suas credenciais, faça uma cópia e remova-o manualmente antes de configurar um banco novo.'
}
if ((Test-Path $dataDirectory) -and (Get-ChildItem $dataDirectory -Force | Select-Object -First 1) -and -not (Test-Path (Join-Path $dataDirectory 'auto.cnf'))) {
    throw "A pasta de dados $dataDirectory já contém arquivos. A configuração foi interrompida para preservar os dados existentes."
}

function New-Secret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [System.BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

function Invoke-NativeCommand {
    param([string]$Path, [string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Path @Arguments 2>$null | Out-Null
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

$rootPassword = New-Secret
$appPassword = New-Secret
New-Item -Path $dataDirectory -ItemType Directory -Force | Out-Null

$config = @"
[mysqld]
basedir="$($mysqlHome.Replace('\', '/'))"
datadir="$($dataDirectory.Replace('\', '/'))"
port=3306
bind-address=127.0.0.1
mysqlx=0
log-error="$((Join-Path $configDirectory 'mysql-error.log').Replace('\', '/'))"
"@
Set-Content -LiteralPath $configFile -Value $config -Encoding ASCII

if (-not (Test-Path (Join-Path $dataDirectory 'auto.cnf'))) {
    Write-Output 'Preparando o armazenamento do MySQL...'
    $exitCode = Invoke-NativeCommand $mysqld @("--defaults-file=$configFile", '--initialize-insecure', '--console')
    if ($exitCode -ne 0) {
        throw 'O MySQL não conseguiu inicializar a pasta de dados. Consulte C:\ProgramData\MySQL\SIAP\mysql-error.log.'
    }
}

$serverProcess = Start-Process -FilePath $mysqld -ArgumentList @("--defaults-file=`"$configFile`"", '--console') -WindowStyle Hidden -PassThru -RedirectStandardOutput $serverStdout -RedirectStandardError $serverStderr
$deadline = [DateTime]::UtcNow.AddSeconds(60)
$mysqlReady = $false
do {
    Start-Sleep -Milliseconds 500
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) {
        throw 'O servidor MySQL encerrou ao iniciar. Consulte C:\ProgramData\MySQL\SIAP\mysql-error.log.'
    }
    $exitCode = Invoke-NativeCommand $mysql @('--no-defaults', '--protocol=tcp', '--host=127.0.0.1', '--port=3306', '--user=root', '--execute=SELECT 1')
    $mysqlReady = $exitCode -eq 0
} until ($mysqlReady -or [DateTime]::UtcNow -ge $deadline)
if (-not $mysqlReady) {
    throw 'O servidor MySQL não respondeu na porta 3306. Consulte C:\ProgramData\MySQL\SIAP\mysql-error.log.'
}

$setRootPassword = "ALTER USER 'root'@'localhost' IDENTIFIED BY '$rootPassword';"
$exitCode = Invoke-NativeCommand $mysql @('--no-defaults', '--protocol=tcp', '--host=127.0.0.1', '--port=3306', '--user=root', "--execute=$setRootPassword")
if ($exitCode -ne 0) {
    throw 'Não foi possível proteger a conta administradora do MySQL.'
}

New-Item -Path $adminDirectory -ItemType Directory -Force | Out-Null
$adminConfig = @"
[client]
user=root
password=$rootPassword
host=127.0.0.1
port=3306
"@
Set-Content -LiteralPath $adminFile -Value $adminConfig -Encoding ASCII
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$exitCode = Invoke-NativeCommand 'icacls.exe' @($adminFile, '/inheritance:r', '/grant:r', ("{0}:(F)" -f $identity), 'NT AUTHORITY\SYSTEM:(F)')
if ($exitCode -ne 0) {
    throw 'Não foi possível restringir o acesso ao arquivo de administração do MySQL.'
}

$schema = [System.IO.File]::ReadAllText($schemaFile)
$exitCode = Invoke-NativeCommand $mysql @("--defaults-extra-file=$adminFile", "--execute=$schema")
if ($exitCode -ne 0) {
    throw 'Não foi possível criar as tabelas do CALÊ. Consulte C:\ProgramData\MySQL\SIAP\mysql-error.log.'
}

$createAppUser = "CREATE USER 'siap_app'@'127.0.0.1' IDENTIFIED BY '$appPassword'; GRANT SELECT, INSERT, UPDATE, DELETE ON siap_db.* TO 'siap_app'@'127.0.0.1';"
$exitCode = Invoke-NativeCommand $mysql @("--defaults-extra-file=$adminFile", "--execute=$createAppUser")
if ($exitCode -ne 0) {
    throw 'Não foi possível criar o usuário restrito de acesso do CALÊ.'
}

$environment = @"
SIAP_DB_HOST=127.0.0.1
SIAP_DB_PORT=3306
SIAP_DB_USER=siap_app
SIAP_DB_PASSWORD=$appPassword
SIAP_DB_NAME=siap_db
SIAP_APP_HOST=localhost
SIAP_APP_PORT=8000
SIAP_SMTP_HOST=
SIAP_SMTP_PORT=587
SIAP_SMTP_USERNAME=
SIAP_SMTP_PASSWORD=
SIAP_SMTP_FROM=
SIAP_SMTP_USE_SSL=false
SIAP_SMTP_STARTTLS=true
"@
Set-Content -LiteralPath $envFile -Value $environment -Encoding ASCII

Write-Output 'MySQL pronto: banco siap_db e tabelas users, tasks e sessions criados.'
Write-Output "Servidor MySQL iniciado em segundo plano (PID $($serverProcess.Id))."
Write-Output 'Credenciais da aplicação salvas em .env, protegido pelo .gitignore.'
Write-Output "Credencial administradora local protegida em: $adminFile"
