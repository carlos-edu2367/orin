# Chat shell: independent scrolling and composer reveal

The desktop Chat page must retain the web interaction model after adopting the
Home grid: the navigation history and active conversation are two independent
scroll regions within a viewport-height shell.

`.chat` therefore owns the viewport height and clips page overflow. The
workspace navigation uses its existing own `overflow-y: auto`, while
`.chat__scroll` is the only scroll container for the active conversation.

The composer remains overlaid at the bottom of the main grid column. Its footer
spans the full column so hovering anywhere in the lower chat area reveals the
centered input when the user is away from the end of the conversation. At the
end of the conversation, `data-at-bottom="true"` keeps it visible.
