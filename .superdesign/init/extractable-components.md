# Extractable components

## ChatHeader
- Source: `frontend/src/features/conversations/ChatPage.tsx`
- Category: layout
- Description: Conversation identity, navigation, overview and command-palette controls.
- Extractable props: title, provider, modelId, overviewOpen.
- Hardcoded: AgentOS brand mark, graphite/lime visual language.

## ConversationComposer
- Source: `frontend/src/features/conversations/Composer.tsx`
- Category: basic
- Description: Auto-growing message field with send/stop action.
- Extractable props: value, running, disabled, error, placeholder.
- Hardcoded: rounded graphite surface and lime send action.

## CommandPalette
- Source: `frontend/src/components/CommandPalette.tsx`
- Category: basic
- Description: Keyboard-accessible navigation overlay.
- Extractable props: commands, conversations.
- Hardcoded: Ctrl/Cmd+K trigger and portal dialog treatment.
