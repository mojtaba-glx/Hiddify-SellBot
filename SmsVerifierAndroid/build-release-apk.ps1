$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$AndroidStudioJbr = "C:\Program Files\Android\Android Studio\jbr"
if (Test-Path $AndroidStudioJbr) {
    $env:JAVA_HOME = $AndroidStudioJbr
    $env:Path = "$env:JAVA_HOME\bin;$env:Path"
}

Write-Host "Project: $ProjectDir"
Write-Host "JAVA_HOME: $env:JAVA_HOME"
java -version

$LocalProperties = Join-Path $ProjectDir "local.properties"
if (-not (Test-Path $LocalProperties)) {
    $DefaultSdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
    if (Test-Path $DefaultSdk) {
        $EscapedSdk = $DefaultSdk.Replace("\", "\\")
        Set-Content -Path $LocalProperties -Value "sdk.dir=$EscapedSdk" -Encoding ASCII
        Write-Host "local.properties created: sdk.dir=$DefaultSdk"
    }
}

$KeystorePath = Join-Path $ProjectDir "sellbot-release.jks"
$KeystorePropertiesPath = Join-Path $ProjectDir "keystore.properties"
if (-not (Test-Path $KeystorePropertiesPath)) {
    $Password = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")).Substring(0, 32)
    $Properties = @(
        "storeFile=sellbot-release.jks",
        "storePassword=$Password",
        "keyAlias=sellbot",
        "keyPassword=$Password"
    )
    Set-Content -Path $KeystorePropertiesPath -Value $Properties -Encoding ASCII
    Write-Host "keystore.properties created."
}

if (-not (Test-Path $KeystorePath)) {
    $KeyProps = @{}
    Get-Content $KeystorePropertiesPath | ForEach-Object {
        if ($_ -match "^\s*([^#=]+)=(.*)$") {
            $KeyProps[$matches[1].Trim()] = $matches[2].Trim()
        }
    }
    & keytool -genkeypair `
        -v `
        -keystore $KeystorePath `
        -storepass $KeyProps["storePassword"] `
        -keypass $KeyProps["keyPassword"] `
        -alias $KeyProps["keyAlias"] `
        -keyalg RSA `
        -keysize 2048 `
        -validity 10000 `
        -dname "CN=SellBot SMS Verifier, OU=SellBot, O=Hiddify SellBot, L=Tehran, S=Tehran, C=IR"
    Write-Host "release keystore created: $KeystorePath"
}

.\gradlew.bat --stop
.\gradlew.bat clean :app:assembleRelease

$BuildGradle = Join-Path $ProjectDir "app\build.gradle"
$VersionName = "release"
if (Test-Path $BuildGradle) {
    $VersionLine = Select-String -Path $BuildGradle -Pattern 'versionName\s+"([^"]+)"' | Select-Object -First 1
    if ($VersionLine -and $VersionLine.Matches.Count -gt 0) {
        $VersionName = $VersionLine.Matches[0].Groups[1].Value
    }
}

$ApkPath = Join-Path $ProjectDir "app\build\outputs\apk\release\app-release.apk"
$FriendlyApkPath = Join-Path $ProjectDir "app\build\outputs\apk\release\SellBotSmsVerifier-v$VersionName-release.apk"
if (Test-Path $ApkPath) {
    Copy-Item $ApkPath $FriendlyApkPath -Force
}

Write-Host ""
Write-Host "Release APK ready:"
Write-Host $FriendlyApkPath
Write-Host ""
Write-Host "Important: keep sellbot-release.jks and keystore.properties safe."
