# Layouts

## Chat shell — `frontend/src/features/conversations/ChatPage.tsx`

The chat page uses a three-row CSS grid: header, independently scrollable conversation body, and composer footer. The header contains product navigation, conversation title/model, overview control, and command palette. The body owns message/event ordering and the overview side panel. The footer owns `Composer` and real-time connection status.

## Application entry — `frontend/src/main.tsx`

```tsx
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>,
)
```

Global layouts are custom React/CSS; no third-party component library is used.
