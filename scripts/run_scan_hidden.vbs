' Launches scheduled_scan.bat with no visible console window.
' Task Scheduler runs this via wscript.exe so the hourly scan never
' flashes a terminal.
Dim fso, sh, scriptDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & scriptDir & "\scheduled_scan.bat""", 0, False
