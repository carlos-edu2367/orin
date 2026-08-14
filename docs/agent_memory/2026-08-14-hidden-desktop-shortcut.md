# Hidden desktop shortcut launcher

The desktop shortcut must preserve `orin.exe --desktop` as the startup
authority, but a console-subsystem PyInstaller launcher shows a terminal when a
`.lnk` invokes it directly. The release installer now writes a fixed local
`orin-desktop.vbs` launcher in `%LOCALAPPDATA%\Orin\bin`; `wscript.exe` runs it
without a console while it starts the normal desktop command in the background.

Existing `Orin Desktop.lnk` files are refreshed during an update without asking
again. New installations still ask whether to create one.
