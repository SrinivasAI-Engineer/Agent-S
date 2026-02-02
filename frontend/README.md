# Frontend (React Agent Inbox)

This is the **Agent Inbox** UI for Human-in-the-Loop (HITL) control.

It lists executions that are **awaiting human actions** or **awaiting re-auth**, and lets you:

- Review Twitter + LinkedIn drafts independently
- Preview the selected in-article image
- Edit Twitter text without affecting LinkedIn (and vice versa)
- Approve/reject content
- Approve/reject image
- Regenerate Twitter only / LinkedIn only
- Submit actions to resume the LangGraph execution

## Run

```bash
npm install
npm run dev
```

Configure backend base URL in `.env`:

```
VITE_API_BASE=http://localhost:8000
```

