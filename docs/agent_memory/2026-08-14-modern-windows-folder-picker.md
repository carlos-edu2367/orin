# Modern Windows folder picker

- The workspace directory action now uses the Windows Shell `IFileOpenDialog` with `FOS_PICKFOLDERS`, `FOS_FORCEFILESYSTEM`, and `FOS_PATHMUSTEXIST`.
- The COM interface is invoked on an STA thread and uses the inherited `IModalWindow.Show` slot when reading the `IFileDialog` vtable. The HRESULT binding uses `ctypes.c_long` because this Python runtime does not expose `wintypes.HRESULT`.
- The local launcher may run the backend without an interactive console. When `pythonw.exe` is available beside the active interpreter, the picker is launched through the same module in that GUI process so the native library dialog is visible; the legacy PowerShell `FolderBrowserDialog` is never used by the default Windows route.
- Visual validation on Windows showed the modern shell dialog with the navigation library tree, breadcrumb path, folder field, and “Selecionar pasta” action. The browser fallback remains available while the native request is pending.
