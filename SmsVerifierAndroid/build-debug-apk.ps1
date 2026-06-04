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

.\gradlew.bat --stop
.\gradlew.bat clean :app:assembleDebug

$ApkPath = Join-Path $ProjectDir "app\build\outputs\apk\debug\app-debug.apk"
$BuildGradle = Join-Path $ProjectDir "app\build.gradle"
$VersionName = "debug"
if (Test-Path $BuildGradle) {
    $VersionLine = Select-String -Path $BuildGradle -Pattern 'versionName\s+"([^"]+)"' | Select-Object -First 1
    if ($VersionLine -and $VersionLine.Matches.Count -gt 0) {
        $VersionName = $VersionLine.Matches[0].Groups[1].Value
    }
}
$FriendlyApkPath = Join-Path $ProjectDir "app\build\outputs\apk\debug\SellBotSmsVerifier-v$VersionName-debug.apk"
if (Test-Path $ApkPath) {
    Copy-Item $ApkPath $FriendlyApkPath -Force
}
Write-Host ""
Write-Host "APK ready:"
Write-Host $FriendlyApkPath
