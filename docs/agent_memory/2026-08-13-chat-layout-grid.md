# Chat layout aligned with Home

The Chat page renders `WorkspaceNavigation` as a direct child of `.chat`, just
like Home. Its previous block layout and fixed `.chat__bar` meant that the
sidebar began at the viewport top and was covered by the header.

Use the shared two-column grid pattern for `.chat`: the header spans row 1,
the navigation occupies column 1 of row 2 with its own vertical scrolling,
and the chat body plus composer share column 2 of row 2. At widths below
900px, hide the navigation and move the chat body/composer to the only column.

This keeps the message composer inside the main content column and reserves
space below the message thread so content is not obscured by the composer.
