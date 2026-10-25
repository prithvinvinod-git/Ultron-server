---
name: t272-ui-enhancements
description: Added UI panels for notifications, conversations, and model catalog
metadata:
  type: project
---

Added three new UI panels to the Ultron Electron app command center:

1. **Notifications panel** (`UIPROTO/src/components/ultron/notifications.tsx`)
   - Shows recent alerts from the event log (GET /notifications)
   - Displays unread count badge in the panel header
   - Includes a test-send form to verify notification channels
   - Styled with level-based coloring (info/warning/error/critical)

2. **Conversations panel** (`UIPROTO/src/components/ultron/conversations.tsx`)
   - Lists the caller's active conversation threads (GET /conversations)
   - Allows renaming and deleting conversations
   - Opens selected conversation in the chat surface
   - Follows the Arc Reactor theme with status dots indicating activity

3. **Models panel** (`UIPROTO/src/components/ultron/models.tsx`)
   - Displays configured model providers and available models (GET /models)
   - Groups models by provider and highlights local/cloud, tool-capable, streaming-capable
   - Shows the default/fallback chain from settings

Integrated the panels into the workspace:
- Added `notifications`, `conversations`, and `models` to `TabId` type (`UIPROTO/src/lib/workspace.tsx`)
- Added corresponding entries to `TABS` array with icons and labels
- Updated `UIPROTO/src/components/ultron/shell/registry.tsx` to render the new panels
- Updated `UIPROTO/src/routes/index.tsx` to route the tabs to the new PanelGrid instances
- Extended the notifications API client (`UIPROTO/src/lib/api.ts`) with providers and test endpoints

These enhancements wire up backend features that were already implemented but not exposed in the UI,
providing visibility into system notifications, conversation history, and model configuration.

Related files:
- `UIPROTO/src/components/ultron/notifications.tsx`
- `UIPROTO/src/components/ultron/conversations.tsx`
- `UIPROTO/src/components/ultron/models.tsx`
- `UIPROTO/src/lib/workspace.tsx`
- `UIPROTO/src/components/ultron/shell/registry.tsx`
- `UIPROTO/src/routes/index.tsx`
- `UIPROTO/src/lib/api.ts`

See also: [[t270-observability-additions]], [[t271-load-shedding-order]]
**Why:** The Ultron server already provided REST endpoints for notifications, conversations, and model catalog, but the Electron app lacked corresponding UI surfaces. Adding these panels brings the command center closer to feature-parity with the server capabilities.
**How to apply:** The changes are additive and follow the existing patterns in the codebase. No breaking changes were introduced.