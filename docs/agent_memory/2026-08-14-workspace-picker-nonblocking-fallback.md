# Non-blocking workspace directory fallback

## Problem

`WorkspaceFolderButton` used one `busy` flag for the Windows native picker, manual path inspection, attach, and detach. The native picker is a blocking subprocess request, so opening it disabled the manual input and made the panel appear to load forever when the dialog was hidden or unavailable.

## Decision

- Keep native-picker state separate from attach/detach state and manual inspection state.
- Leave the manual path field and its action available while the native picker request is pending.
- Track inspection request order so a late native-picker response cannot overwrite a newer manually selected path.
- Show an explicit status explaining that the Windows picker is open and that the path can be pasted as a fallback.
- Use a non-wait disabled cursor for the panel controls so a pending native request does not communicate an unbounded UI lock.

## Validation

The local server was started on `127.0.0.1:8001` with an isolated temporary `ORIN_HOME`. Browser validation opened the workspace panel, started the native picker, entered a real local repository path while that request was pending, clicked `Adicionar diretório`, and reached the directory confirmation state. The temporary picker process was then closed; no workspace was attached.
