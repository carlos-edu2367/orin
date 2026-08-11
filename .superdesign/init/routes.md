# Routes

React 19 with React Router, configured in `frontend/src/app/routes.tsx`.

| Path | Component | Layout |
| --- | --- | --- |
| `/` | `frontend/src/app/Home.tsx` | Home grid shell |
| `/chats/:conversationId` | `frontend/src/features/conversations/ChatPage.tsx` | Chat grid shell |
| `/chats/:conversationId/overview` | `ChatPage.tsx` | Chat plus overview side panel |
| `/providers` | `ProviderSettingsPage.tsx` | App shell |

The conversation route is the target for this design task.
