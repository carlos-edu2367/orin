# Hidden desktop shortcut launcher

The desktop shortcut must preserve `orin.exe --desktop` as the startup
authority, but a console-subsystem PyInstaller launcher shows a terminal when a
`.lnk` invokes it directly. The release installer writes a fixed local
`orin-desktop.ps1` launcher in `%LOCALAPPDATA%\Orin\bin` and targets inbox
Windows PowerShell with `-WindowStyle Hidden`, which starts the normal desktop
command in the background without exposing a terminal.

Existing `Orin Desktop.lnk` files are refreshed during an update without asking
again. New installations still ask whether to create one.
