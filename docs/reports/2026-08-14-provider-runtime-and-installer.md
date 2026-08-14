# Provider runtime and installer verification

## Finding

The provider setup failure at the local API was caused by catalog timestamp
deserialization, not by a worker or by provider credentials. SQLite returns the
catalog `refreshed_at` value without timezone information. The domain catalog
record requires an aware timestamp, so the post-save model list endpoint failed
with HTTP 500 and the frontend showed its generic unavailable message.

## Correction

The catalog repository now assigns UTC only when a database value is naive.
PostgreSQL values that already contain a timezone are unchanged. A SQLite unit
test covers the save/read path.

## Runtime and release behavior

Provider setup and catalog refresh execute in the API process. They do not
depend on the chat workers; the local runtime had both expected worker
heartbeats. The downloaded release remains unchanged until a new release is
built and published from this corrected source.

The installed launcher now supports `orin --uninstall`. It stops Orin, removes
the command, shortcut, runtime, data and configuration. A separate, scoped
PowerShell cleanup process waits for the executable to exit so Windows file
locks cannot leave a partial installation. It validates its two deletion roots
against the known LocalAppData Orin directories and the command refuses to run
from a source checkout.

The desktop host caches the release metadata for one day. When that metadata
contains a newer version, it now retains a Windows taskbar update flag on later
launches as well as showing the existing opt-in update dialog when first found.
No automatic download or installation is performed.
