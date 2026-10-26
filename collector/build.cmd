@echo off
setlocal
cd /d "%~dp0"
set "FX=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319"
set "GAC=%WINDIR%\Microsoft.NET\assembly\GAC_MSIL"
"%FX%\csc.exe" /nologo /target:exe /out:Probe.exe /win32manifest:Probe.manifest /reference:System.Web.Extensions.dll /reference:"%GAC%\UIAutomationClient\v4.0_4.0.0.0__31bf3856ad364e35\UIAutomationClient.dll" /reference:"%GAC%\UIAutomationTypes\v4.0_4.0.0.0__31bf3856ad364e35\UIAutomationTypes.dll" /reference:"%GAC%\WindowsBase\v4.0_4.0.0.0__31bf3856ad364e35\WindowsBase.dll" Probe.cs
if errorlevel 1 exit /b %errorlevel%
"%FX%\csc.exe" /nologo /target:exe /out:PowerWatch.exe /win32manifest:Probe.manifest /reference:System.Web.Extensions.dll /reference:System.Windows.Forms.dll PowerWatch.cs
exit /b %errorlevel%
