"""Route-level tests for ``/auth`` and the request-context middleware (T021).

The service tests in ``test_authentication.py`` cover *what* the authenticator
decides. These cover the parts only the assembled application can answer:

* the mounted surface is exactly the five intended routes;
* a domain error becomes the documented status and error code, not a 500;
* the correlation id is honoured, echoed, and replaced when untrusted.

The container is stubbed at the ``ContainerProtocol`` boundary rather than by
standing up PostgreSQL. Everything below that boundary is already covered
against real repositories elsewhere; what is under test here is the wiring, so a
second database would add cost without adding coverage.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.dependencies import CORRELATION_HEADERS, MAX_CORRELATION_LENGTH
from app.api.routes.auth import router as auth_router
from app.config import get_settings
from app.main import RequestContextMiddleware, add_exception_handlers
from app.security.audit import AuditLogger

if TYPE_CHECKING:
    from app.database.models import User

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Container stub
# --------------------------------------------------------------------------- #


#: Staging buffers for the in-flight request scopes, keyed by the token each
#: scope hands out as its session. One buffer per scope, so a nested scope's
#: committed rows are not discarded by the outer rollback.
_staging: dict[object, list[dict[str, Any]]] = {}


class StubContainer:
    """The slice of the container the dependencies reach for.

    The repository factories return the same in-memory doubles the service tests
    use, ignoring the session. That is the point of stubbing here: the request
    transaction is already covered against a real database, and these tests are
    about which dependencies a route pulls in and what it then returns.

    Returning the doubles rather than a pre-built :class:`Authenticator` matters.
    ``get_authenticator`` constructs one per request from four factory calls, so a
    stub that handed back a finished object would skip exactly the wiring these
    tests exist to check.

    ``session_scope`` deliberately models the real rollback. Rows appended during
    a request are staged and only published on a clean exit, so a test can observe
    what a client error costs the audit trail. That behaviour is not incidental:
    it is what makes the durability test below meaningful.
    """

    def __init__(self, repositories: dict[str, Any], settings: Any) -> None:
        self._repositories = repositories
        self._settings = settings
        self.scope_entries = 0
        self.commits = 0
        self.rollbacks = 0
        self._depth = 0

    @property
    def settings(self) -> Any:
        """The full ``Settings``; ``get_authenticator`` reads ``.security``."""
        return self._settings

    @property
    def committed_audit_rows(self) -> list[dict[str, Any]]:
        """Audit rows that survived a commit."""
        rows: list[dict[str, Any]] = self._repositories["audit"].rows
        return rows

    def session_scope(self) -> Any:
        """Model the real scope, including that a rollback discards the work.

        Each scope gets a staging buffer keyed by its own token, and only the
        top-level scope's exit is allowed to roll the request back. That nested
        distinction matters: ``DurableAuditSink`` opens a second scope to write
        outside the request transaction, and without it a durable row would be
        swallowed by the very rollback it exists to escape.
        """
        outer = self
        outer._depth += 1
        is_top_level = outer._depth == 1
        token = object()

        class _Scope:
            async def __aenter__(self) -> Any:
                outer.scope_entries += 1
                _staging[token] = []
                return token

            async def __aexit__(self, *exc: object) -> None:
                rows = _staging.pop(token, [])
                outer._depth -= 1
                if exc[0] is None:
                    outer.commits += 1
                    outer._repositories["audit"].rows.extend(rows)
                elif is_top_level:
                    # The real session_scope rolls back, discarding everything
                    # written during the request -- including audit rows.
                    outer.rollbacks += 1

        return _Scope()

    def get_user_repository(self, session: Any = None) -> Any:
        return self._repositories["users"]

    def get_session_repository(self, session: Any = None) -> Any:
        return self._repositories["sessions"]

    def get_device_repository(self, session: Any = None) -> Any:
        return self._repositories["devices"]

    def get_audit_log_repository(self, session: Any = None) -> Any:
        """Stage into the current scope's buffer, or write straight through.

        ``DurableAuditSink`` passes no session, which is what marks it as being
        outside any request scope; those rows are committed as they arrive.
        """
        target = _staging.get(session) if session is not None else None
        sink = self._repositories["audit"]

        class _Scoped:
            async def record(self, **fields: Any) -> None:
                if target is None:
                    sink.rows.append(fields)
                else:
                    target.append(fields)

        return _Scoped()


class Issued:
    """The two tokens from a login, for readability at the call site."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.access_token: str = body["access_token"]
        self.refresh_token: str = body["refresh_token"]


@pytest.fixture
def users(authenticator: Any) -> Any:
    """The user repository, so a test can seed an account before logging in."""
    return authenticator[1]


@pytest.fixture
def full_settings(security: Any) -> Any:
    """Top-level ``Settings`` carrying the cheap Argon2 cost.

    ``get_authenticator`` reads ``container.settings.security``, so the stub needs
    the whole settings object rather than just the security section.
    """
    return get_settings().model_copy(update={"security": security})


@pytest.fixture
def app(authenticator: Any, full_settings: Any, audit_sink: Any) -> FastAPI:
    """An app with only the auth router, but the real middleware and handlers.

    The exception handlers are not optional here. A domain error raised inside a
    dependency does not become a status code by itself -- ``create_app`` installs
    the handlers that translate it -- so a bare ``FastAPI()`` lets ``AuthError``
    escape as an unhandled exception and every 401 assertion fails for a reason
    that has nothing to do with authentication.
    """
    _service, users, sessions, devices = authenticator
    application = FastAPI()
    application.state.container = StubContainer(
        {
            "users": users,
            "sessions": sessions,
            "devices": devices,
            "audit": audit_sink,
        },
        full_settings,
    )
    add_exception_handlers(application)
    application.add_middleware(
        RequestContextMiddleware,
        candidates=CORRELATION_HEADERS,
        max_length=MAX_CORRELATION_LENGTH,
    )
    application.include_router(auth_router)
    return application


@pytest.fixture
def client(app: FastAPI) -> Any:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def _no_durable_sink(monkeypatch: Any) -> Iterator[None]:
    """Build authenticators without a durable sink, to pin the fallback.

    ``durable=True`` is documented as degrading to the ordinary request-scoped
    behaviour when no durable sink is configured, rather than raising. A caller
    should not have to know whether one is wired, so that fallback is pinned
    here: a refusal is still audited, it just shares the transaction and is lost
    to the rollback. That is the whole reason the real wiring supplies a sink.
    """
    real = AuditLogger.__init__

    def without_durable(self: Any, repository: Any, *, strict: bool = False, **_: Any) -> None:
        real(self, repository, strict=strict)

    monkeypatch.setattr(AuditLogger, "__init__", without_durable)
    yield
    monkeypatch.setattr(AuditLogger, "__init__", real)


@pytest.fixture
def account(users: Any, make_user: Any) -> User:
    """One seeded, active, non-privileged user with a known password."""
    user: User = make_user()
    users.users.append(user)
    return user


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def login(client: Any, username: str) -> Issued:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200, response.text
    return Issued(response.json())


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# The surface itself
# --------------------------------------------------------------------------- #


def test_only_the_five_intended_routes_are_mounted() -> None:
    """A route that is not in the spec should not exist yet.

    Cheap to assert, and it is the assertion that fails when someone adds an
    endpoint as a "temporary" measure and it is still there a year later.
    """
    paths = {
        (route.path, tuple(sorted(route.methods or ())))
        for route in auth_router.routes
        if isinstance(route, APIRoute)
    }
    assert paths == {
        ("/auth/login", ("POST",)),
        ("/auth/refresh", ("POST",)),
        ("/auth/logout", ("POST",)),
        ("/auth/me", ("GET",)),
        ("/auth/password", ("POST",)),
    }


def test_login_does_not_advertise_its_tokens_as_safe_to_log(client: Any) -> None:
    """The response schema must not invite a client to persist the body."""
    schema = client.get("/openapi.json").json()
    body = schema["components"]["schemas"]["TokenResponse"]
    assert set(body["properties"]) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_at",
        "refresh_expires_at",
        "session_id",
    }


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #


def test_login_issues_a_pair(client: Any, account: User) -> None:
    issued = login(client, account.username)

    assert issued.access_token
    assert issued.refresh_token != issued.access_token
    assert client.get("/auth/me", headers=bearer(issued.access_token)).status_code == 200


def test_login_returns_the_same_401_for_every_kind_of_failure(
    client: Any,
    account: User,
    users: Any,
) -> None:
    """The response must not reveal which half of the credential was wrong.

    If a wrong password, an unknown username and a disabled account differ in
    message or code, the endpoint is a reliable oracle for which usernames exist
    and which are worth attacking. A disabled account is the interesting case:
    "this account is locked" is the kind of message an attacker can use to find
    out whether a guessed username was real.
    """
    wrong_password = client.post(
        "/auth/login",
        json={"username": account.username, "password": "not the password"},
    )
    unknown_user = client.post(
        "/auth/login",
        json={"username": "nobody", "password": "correct horse battery staple"},
    )
    account.is_active = False
    try:
        disabled = client.post(
            "/auth/login",
            json={"username": account.username, "password": "correct horse battery staple"},
        )
    finally:
        account.is_active = True
    assert users.users, "the disabled-account case needs a seeded account"

    for response in (wrong_password, unknown_user, disabled):
        assert response.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    assert unknown_user.json() == disabled.json()


def test_a_refusal_is_still_audited_without_a_durable_sink(
    client: Any,
    account: User,
    _no_durable_sink: None,
) -> None:
    """Documenting the cost of the fallback, so the sink is not wired away.

    With no durable sink the refusal shares the request transaction and is lost
    to the rollback -- the client still gets a correct 401, and the audit table
    still looks empty. That is exactly the failure the durable sink exists to
    prevent, so it is asserted rather than left as folklore.
    """
    container = client.app.state.container

    response = client.post(
        "/auth/login",
        json={"username": account.username, "password": "guess"},
    )

    assert response.status_code == 401
    assert container.committed_audit_rows == []


def test_a_disabled_account_cannot_log_in(client: Any, account: User) -> None:
    """Deactivation has to actually stop access, not merely be recorded."""
    account.is_active = False
    try:
        response = client.post(
            "/auth/login",
            json={"username": account.username, "password": "correct horse battery staple"},
        )
    finally:
        account.is_active = True
    assert response.status_code == 401


def test_login_rejects_an_unknown_field(client: Any) -> None:
    """A mistyped field name should be a 422, not a silently ignored argument."""
    response = client.post(
        "/auth/login",
        json={"username": "a", "password": "b", "passwrod": "typo"},
    )
    assert response.status_code == 422


def test_login_enforces_a_maximum_password_length(client: Any) -> None:
    """An unbounded password is a free way to make the server do Argon2 work."""
    response = client.post(
        "/auth/login",
        json={"username": "a", "password": "x" * 1025},
    )
    assert response.status_code == 422


def test_login_audits_the_attempt(client: Any, account: User) -> None:
    container = client.app.state.container
    login(client, account.username)

    assert container.commits == 1
    assert "auth.login" in [str(row["action"]) for row in container.committed_audit_rows]


def test_a_failed_login_is_audited_even_though_the_request_rolls_back(
    client: Any,
    account: User,
) -> None:
    """A denial that is rolled back is not a denial anyone can investigate.

    The request raises, ``session_scope`` rolls back, and an audit row written in
    the same transaction goes with it. The credential was still presented and
    still refused, which is precisely the event §15 requires to be recorded -- so
    the audit write has to reach a database outside the request transaction, or
    the login-failure trail is empty exactly when it is needed.

    This is the failure mode that makes brute-force detection impossible: the
    table would look clean while the endpoint was under attack.
    """
    container = client.app.state.container

    response = client.post(
        "/auth/login",
        json={"username": account.username, "password": "guess"},
    )

    assert response.status_code == 401
    assert container.rollbacks == 1
    assert "auth.login_failed" in [str(row["action"]) for row in container.committed_audit_rows]


# --------------------------------------------------------------------------- #
# Authenticated routes
# --------------------------------------------------------------------------- #


def test_me_describes_the_caller(client: Any, account: User) -> None:
    issued = login(client, account.username)

    response = client.get("/auth/me", headers=bearer(issued.access_token))

    assert response.status_code == 200
    assert response.json() == {
        "actor_type": "user",
        "username": account.username,
        "is_superuser": False,
    }


def test_me_without_a_token_is_401(client: Any) -> None:
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "Bearer not-a-real-token",
        "Bearer",
        "bearer abc",
        "Basic dXNlcjpwYXNz",
        "Token abc",
        "Bearer ../../etc/passwd",
    ],
)
def test_a_credential_that_is_not_a_bearer_token_is_refused(client: Any, header: str) -> None:
    """Only one scheme, and only a well-formed value.

    Accepting several schemes means every one of them is an authentication path
    that has to be kept just as safe as the others.
    """
    response = client.get("/auth/me", headers={"Authorization": header})
    assert response.status_code == 401, header


def test_password_change_revokes_every_session_for_the_account(client: Any, account: User) -> None:
    """The old sessions must stop working, or the change achieved nothing.

    A password change is the usual response to a suspected compromise, so
    leaving previously issued sessions alive would defeat the purpose.
    """
    first = login(client, account.username)
    second = login(client, account.username)

    changed = client.post(
        "/auth/password",
        json={
            "current_password": "correct horse battery staple",
            "new_password": "a different long passphrase",
        },
        headers=bearer(first.access_token),
    )

    assert changed.status_code == 204, changed.text
    assert client.get("/auth/me", headers=bearer(second.access_token)).status_code == 401
    assert client.get("/auth/me", headers=bearer(first.access_token)).status_code == 401
    assert (
        client.post(
            "/auth/login",
            json={"username": account.username, "password": "a different long passphrase"},
        ).status_code
        == 200
    )


def test_password_change_with_the_wrong_current_password_is_401(client: Any, account: User) -> None:
    issued = login(client, account.username)

    response = client.post(
        "/auth/password",
        json={
            "current_password": "wrong",
            "new_password": "a different long passphrase",
        },
        headers=bearer(issued.access_token),
    )

    assert response.status_code == 401
    # And the old password still works, so a failed attempt changed nothing.
    assert login(client, account.username).access_token


def test_password_change_requires_a_long_enough_new_password(client: Any, account: User) -> None:
    issued = login(client, account.username)

    response = client.post(
        "/auth/password",
        json={"current_password": "correct horse battery staple", "new_password": "short"},
        headers=bearer(issued.access_token),
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Refresh and logout
# --------------------------------------------------------------------------- #


def test_refresh_rotates_the_pair_and_spends_the_old_one(client: Any, account: User) -> None:
    issued = login(client, account.username)

    response = client.post("/auth/refresh", json={"refresh_token": issued.refresh_token})

    assert response.status_code == 200
    rotated = Issued(response.json())
    assert rotated.refresh_token != issued.refresh_token
    assert client.get("/auth/me", headers=bearer(rotated.access_token)).status_code == 200

    # Replaying the spent token is refused.
    replay = client.post("/auth/refresh", json={"refresh_token": issued.refresh_token})
    assert replay.status_code == 401


def test_refresh_is_not_accepted_with_an_access_token(client: Any, account: User) -> None:
    """The two token kinds are not interchangeable.

    If an access token worked here, the short-lived credential would become a
    long-lived one and §30's separation would be decorative.
    """
    issued = login(client, account.username)
    response = client.post("/auth/refresh", json={"refresh_token": issued.access_token})
    assert response.status_code == 401


def test_logout_succeeds_without_a_usable_token(client: Any) -> None:
    """A client whose token has expired must still be able to log out.

    This is exactly the moment someone reaches for logout, so a 401 here would
    leave the session sitting in the table until it aged out on its own.
    """
    assert client.post("/auth/logout").status_code == 204
    spent = client.post("/auth/logout", headers={"Authorization": "Bearer already-expired"})
    assert spent.status_code == 204


def test_logout_revokes_the_session_it_is_given(client: Any, account: User) -> None:
    issued = login(client, account.username)

    response = client.post("/auth/logout", headers=bearer(issued.access_token))

    assert response.status_code == 204
    assert client.get("/auth/me", headers=bearer(issued.access_token)).status_code == 401


# --------------------------------------------------------------------------- #
# Correlation ids
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("header", CORRELATION_HEADERS)
def test_a_client_correlation_id_is_echoed(client: Any, header: str) -> None:
    response = client.get("/auth/me", headers={header: "abc-123"})
    assert response.headers.get("X-Request-ID") == "abc-123"


@pytest.mark.parametrize(
    "hostile",
    [
        "a" * 500,
        "has space",
        "quote\"and'quote",
        "<script>alert(1)</script>",
        "semi;colon",
        "back\\slash",
    ],
)
def test_an_untrusted_correlation_id_is_replaced_not_reflected(client: Any, hostile: str) -> None:
    """Log injection is the reason for the character allow-list.

    The value is echoed in a response header and written into log lines, so it
    has to be safe to do both. Anything outside the allow-list is discarded and
    replaced rather than sanitised, because a filter is a list of bypasses.
    """
    response = client.get("/auth/me", headers={"X-Request-ID": hostile})
    echoed = response.headers.get("X-Request-ID", "")
    assert echoed != hostile
    assert len(echoed) <= 128


def test_a_request_without_a_correlation_id_still_gets_one(client: Any) -> None:
    """Every response carries an id, so a log line can always be tied to one."""
    assert client.get("/auth/me").headers.get("X-Request-ID")


def test_the_first_recognised_header_wins(client: Any) -> None:
    """Two ids is a choice, and it has to be made the same way every time."""
    response = client.get(
        "/auth/me",
        headers={"X-Request-ID": "first", "X-Correlation-ID": "second"},
    )
    assert response.headers.get("X-Request-ID") == "first"
