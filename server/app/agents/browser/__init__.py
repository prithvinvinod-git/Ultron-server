"""The browser agent (Phase 6, spec section 11).

:class:`~app.agents.browser.agent.BrowserAgent` is §11's worker. It holds no
Playwright reference and no session: it drives §16's model → tool → model cycle
(T068, :class:`~app.agents.loop.ToolCallingLoop`) over the ``browser.*`` tool
family (T112), and the per-agent session isolation §11 requires is enforced
underneath by the session manager (T111).
"""

from app.agents.browser.agent import BrowserAgent

__all__ = ["BrowserAgent"]
