@echo off
cd /d "%~dp0"
echo Creating "CMA Editor" shortcut on your Desktop...

set "_vbs=%TEMP%\cma_shortcut_%RANDOM%.vbs"
(
    echo Set oWS = WScript.CreateObject("WScript.Shell"^)
    echo sLink = oWS.SpecialFolders("Desktop"^) ^& "\CMA Editor.lnk"
    echo Set oLink = oWS.CreateShortcut(sLink^)
    echo oLink.TargetPath      = "%~dp0start.bat"
    echo oLink.WorkingDirectory = "%~dp0"
    echo oLink.IconLocation    = "%~dp0icon\cma.ico,0"
    echo oLink.Description     = "CMA Editor"
    echo oLink.Save
) > "%_vbs%"
wscript.exe "%_vbs%"
del "%_vbs%" 2>nul

echo.
echo  Done!  A "CMA Editor" shortcut has been added to your Desktop.
echo  Double-click it any time to launch the app.
echo.
pause
