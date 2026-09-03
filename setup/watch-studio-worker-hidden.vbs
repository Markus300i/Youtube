Option Explicit

Dim fileSystem, shell, watchdogScript, command, argument, exitCode

Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

watchdogScript = fileSystem.BuildPath( _
    fileSystem.GetParentFolderName(WScript.ScriptFullName), _
    "watch-studio-worker.ps1" _
)

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & QuoteArgument(watchdogScript)
For Each argument In WScript.Arguments
    command = command & " " & QuoteArgument(argument)
Next

exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode

Function QuoteArgument(value)
    QuoteArgument = Chr(34) & CStr(value) & Chr(34)
End Function
