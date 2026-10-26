@echo off
setlocal
cd /d "%~dp0"
call collector\build.cmd
if errorlevel 1 exit /b %errorlevel%
set "FX=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319"
set "GAC=%WINDIR%\Microsoft.NET\assembly\GAC_MSIL"
if not exist "%FX%\csc.exe" (
  echo Windows .NET Framework compiler was not found. See README.md.
  exit /b 1
)
"%FX%\csc.exe" /nologo /out:NotificationBridge.exe /reference:System.Web.Extensions.dll /reference:"%GAC%\System.Runtime\v4.0_4.0.0.0__b03f5f7f11d50a3a\System.Runtime.dll" /reference:"%WINDIR%\System32\WinMetadata\Windows.UI.winmd" /reference:"%WINDIR%\System32\WinMetadata\Windows.Foundation.winmd" /reference:"%WINDIR%\System32\WinMetadata\Windows.ApplicationModel.winmd" NotificationBridge.cs
exit /b %errorlevel%
