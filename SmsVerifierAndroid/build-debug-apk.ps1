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
Write-Host ""
Write-Host "APK ready:"
Write-Host $ApkPath
