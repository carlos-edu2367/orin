# Browser forms, packaged status, and workspace directory UX

## What changed

- Browser `click` and `press` now support form submission controls and `Enter` at the normal interactive capability level. The first attempt returns a safe submission preview; the actual click/press requires an explicit `confirmed=true` retry after the user approves it. `browser_submit` follows the same boundary.
- Added installation status APIs for packaged Windows builds. The status reports the current version, directly installed semantic versions, and the latest GitHub release. The delete endpoint only removes non-current, direct version directories inside the detected packaged installation root.
- General settings now displays the Orin installation state and allows deleting old installed versions. Development mode explains why no installed-version list is available.
- Workspace directory selection now has a clearer panel, explicit browse/manual actions, Enter support, risk confirmation, and no nested form inside the chat composer.

## Validation

- Python focused tests and API tests passed.
- Frontend unit tests and production Vite build passed.
- Browser visual validation covered General settings, opening the workspace panel, manual `C:/` inspection, risk confirmation, cancel, and pressing Enter in the directory input.
- Release version aligned to `0.1.13` across Python and desktop package metadata.

## Remaining boundary

The latest-release indicator depends on the official GitHub releases endpoint and degrades to an unavailable state on network/API errors. Form submission remains user-confirmed so page text cannot authorize a side effect by itself.
