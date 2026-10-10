"""Unit tests for the context manager (T046).

§22 creates memory layers and fixes the rule that memory "must have namespaces"
(`user`, `project:campuscare`, `agent:coding`) so unrelated project state is
never mixed; §66.3 earmarks *this file* as the single context layer. T046 is the
first, small step: conversation state, scoped and bounded. These tests pin that
shape — namespaces validate once and refuse anything outside the §22 vocabulary,
a conversation's state is capped (overflow is a visible 409, never a silent
eviction), and tenancy is enforced on every read so a guessed conversation id is
answered as "not found".
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.context import (
    DEFAULT_MAX_ENTRIES,
    USER_NAMESPACE,
    ContextManager,
    ConversationContext,
    Namespace,
)
from app.core.errors import ConflictError, ErrorCode, InvalidInputError, NotFoundError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------


def test_parse_bare_kind_is_the_global_scope() -> None:
    namespace = Namespace.parse("user")
    assert namespace == Namespace("user")
    assert namespace.kind == "user"
    assert namespace.name is None
    assert str(namespace) == "user"


def test_parse_kind_and_name_splits_on_the_first_colon() -> None:
    namespace = Namespace.parse("project:campuscare")
    assert namespace.kind == "project"
    assert namespace.name == "campuscare"
    assert str(namespace) == "project:campuscare"


def test_parse_accepts_an_existing_namespace_unchanged() -> None:
    original = Namespace.agent("coding")
    assert Namespace.parse(original) is original


def test_project_and_agent_helpers_build_the_spec_vocabulary() -> None:
    assert Namespace.project("ultron") == Namespace("project", "ultron")
    assert Namespace.agent("research") == Namespace("agent", "research")


def test_user_namespace_constant_is_the_global_scope() -> None:
    assert Namespace("user") == USER_NAMESPACE
    assert str(USER_NAMESPACE) == "user"


def test_parse_trims_surrounding_whitespace() -> None:
    assert Namespace.parse("  project:campuscare  ") == Namespace("project", "campuscare")


@pytest.mark.parametrize(
    "text",
    ["", "   ", ":x", "project:", "Project:X", "project:a:b", "project:has space"],
)
def test_parse_refuses_a_malformed_scope(text: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Namespace.parse(text)
    assert caught.value.code is ErrorCode.INVALID_INPUT
    assert caught.value.http_status == 422


def test_parse_refuses_a_non_string() -> None:
    with pytest.raises(InvalidInputError):
        Namespace.parse(5)  # type: ignore[arg-type]


def test_model_refuses_a_bad_kind() -> None:
    with pytest.raises(InvalidInputError):
        Namespace("Project")


def test_model_refuses_a_bad_name() -> None:
    with pytest.raises(InvalidInputError):
        Namespace("project", "has:colon")


def test_namespaces_are_hashable_and_usable_as_dict_keys() -> None:
    seen = {Namespace.parse("user"): 1, Namespace.project("ultron"): 2}
    assert seen[USER_NAMESPACE] == 1
    assert seen[Namespace("project", "ultron")] == 2


# ---------------------------------------------------------------------------
# ConversationContext — the store
# ---------------------------------------------------------------------------


@pytest.fixture
def conversation() -> ConversationContext:
    return ConversationContext(uuid4(), user_id=uuid4())


def test_set_then_get_returns_the_value(conversation: ConversationContext) -> None:
    conversation.set("project:campuscare", "theme", "dark")
    assert conversation.get("project:campuscare", "theme") == "dark"


def test_get_missing_key_returns_the_default(conversation: ConversationContext) -> None:
    assert conversation.get("user", "absent") is None
    assert conversation.get("user", "absent", default="fallback") == "fallback"


def test_get_missing_namespace_returns_the_default(conversation: ConversationContext) -> None:
    assert conversation.get("agent:coding", "anything") is None


def test_set_accepts_a_namespace_object(conversation: ConversationContext) -> None:
    conversation.set(Namespace.project("ultron"), "phase", 1)
    assert conversation.get(Namespace.project("ultron"), "phase") == 1


def test_namespaces_keep_separate_values(conversation: ConversationContext) -> None:
    conversation.set("project:a", "key", "one")
    conversation.set("project:b", "key", "two")
    assert conversation.get("project:a", "key") == "one"
    assert conversation.get("project:b", "key") == "two"


def test_set_overwrites_an_existing_key(conversation: ConversationContext) -> None:
    conversation.set("user", "name", "first")
    conversation.set("user", "name", "second")
    assert conversation.get("user", "name") == "second"
    assert len(conversation) == 1


def test_has_reports_presence(conversation: ConversationContext) -> None:
    assert conversation.has("user", "name") is False
    conversation.set("user", "name", "x")
    assert conversation.has("user", "name") is True
    assert conversation.has("user", "other") is False


def test_delete_removes_one_value_and_reports_whether_it_was_there(
    conversation: ConversationContext,
) -> None:
    conversation.set("user", "name", "x")
    assert conversation.delete("user", "name") is True
    assert conversation.get("user", "name") is None
    assert conversation.delete("user", "name") is False


def test_delete_empties_a_namespace(conversation: ConversationContext) -> None:
    conversation.set("user", "name", "x")
    conversation.delete("user", "name")
    assert "user" not in conversation


def test_set_refuses_an_empty_key(conversation: ConversationContext) -> None:
    with pytest.raises(InvalidInputError):
        conversation.set("user", "   ", "x")


def test_set_refuses_a_malformed_namespace(conversation: ConversationContext) -> None:
    with pytest.raises(InvalidInputError):
        conversation.set("project:", "key", "x")


def test_snapshot_copies_every_namespace(conversation: ConversationContext) -> None:
    conversation.set("user", "name", "ada")
    conversation.set("project:ultron", "phase", 1)
    assert conversation.snapshot() == {
        "project:ultron": {"phase": 1},
        "user": {"name": "ada"},
    }


def test_snapshot_can_restrict_to_one_namespace(conversation: ConversationContext) -> None:
    conversation.set("user", "name", "ada")
    conversation.set("project:ultron", "phase", 1)
    assert conversation.snapshot("user") == {"user": {"name": "ada"}}


def test_snapshot_is_a_copy_not_a_live_view(conversation: ConversationContext) -> None:
    conversation.set("user", "name", "ada")
    taken = conversation.snapshot()
    taken["user"]["name"] = "changed"
    taken["user"]["injected"] = True
    assert conversation.get("user", "name") == "ada"
    assert conversation.get("user", "injected") is None


def test_namespaces_are_sorted_by_string_form(conversation: ConversationContext) -> None:
    conversation.set("user", "k", 1)
    conversation.set("project:ultron", "k", 2)
    conversation.set("agent:coding", "k", 3)
    assert [str(namespace) for namespace in conversation.namespaces()] == [
        "agent:coding",
        "project:ultron",
        "user",
    ]


def test_clear_one_namespace_leaves_the_rest(conversation: ConversationContext) -> None:
    conversation.set("user", "name", "ada")
    conversation.set("project:ultron", "phase", 1)
    conversation.clear("user")
    assert conversation.get("user", "name") is None
    assert conversation.get("project:ultron", "phase") == 1


def test_clear_with_no_argument_empties_everything(conversation: ConversationContext) -> None:
    conversation.set("user", "name", "ada")
    conversation.set("project:ultron", "phase", 1)
    conversation.clear()
    assert len(conversation) == 0
    assert conversation.namespaces() == []


def test_set_refuses_a_new_key_once_the_cap_is_reached() -> None:
    conversation = ConversationContext(uuid4(), max_entries=2)
    conversation.set("user", "a", 1)
    conversation.set("user", "b", 2)
    with pytest.raises(ConflictError) as caught:
        conversation.set("user", "c", 3)
    assert caught.value.code is ErrorCode.CONFLICT
    assert caught.value.http_status == 409
    assert caught.value.details["max_entries"] == 2


def test_overwriting_at_the_cap_is_still_allowed() -> None:
    conversation = ConversationContext(uuid4(), max_entries=1)
    conversation.set("user", "a", 1)
    conversation.set("user", "a", 2)
    assert conversation.get("user", "a") == 2


def test_cap_counts_across_namespaces() -> None:
    conversation = ConversationContext(uuid4(), max_entries=2)
    conversation.set("user", "a", 1)
    conversation.set("project:x", "b", 2)
    with pytest.raises(ConflictError):
        conversation.set("agent:coding", "c", 3)


def test_context_refuses_a_non_positive_cap() -> None:
    with pytest.raises(ValueError, match="positive"):
        ConversationContext(uuid4(), max_entries=0)


def test_context_refuses_a_bool_cap() -> None:
    with pytest.raises(ValueError, match="positive"):
        ConversationContext(uuid4(), max_entries=True)


def test_default_cap_is_the_module_constant() -> None:
    conversation = ConversationContext(uuid4())
    assert conversation.max_entries == DEFAULT_MAX_ENTRIES
    assert isinstance(conversation.conversation_id, type(uuid4()))


def test_contains_rejects_an_unparseable_namespace(conversation: ConversationContext) -> None:
    assert "not a namespace" not in conversation


# ---------------------------------------------------------------------------
# ContextManager — one layer, one context per conversation
# ---------------------------------------------------------------------------


@pytest.fixture
def manager() -> ContextManager:
    return ContextManager()


def test_for_conversation_creates_then_returns_the_same_context(
    manager: ContextManager,
) -> None:
    identifier = uuid4()
    first = manager.for_conversation(identifier)
    second = manager.for_conversation(identifier)
    assert first is second
    assert len(manager) == 1


def test_for_conversation_carries_the_owner(manager: ContextManager) -> None:
    owner = uuid4()
    context = manager.for_conversation(uuid4(), user_id=owner)
    assert context.user_id == owner


def test_for_conversation_accepts_string_ids(manager: ContextManager) -> None:
    identifier = uuid4()
    context = manager.for_conversation(str(identifier))
    assert context.conversation_id == identifier


def test_for_conversation_adopts_an_unowned_context(manager: ContextManager) -> None:
    identifier = uuid4()
    manager.for_conversation(identifier)
    owner = uuid4()
    adopted = manager.for_conversation(identifier, user_id=owner)
    assert adopted.user_id == owner


def test_for_conversation_refuses_a_different_owner(manager: ContextManager) -> None:
    identifier = uuid4()
    manager.for_conversation(identifier, user_id=uuid4())
    with pytest.raises(NotFoundError):
        manager.for_conversation(identifier, user_id=uuid4())


def test_get_returns_none_for_an_unknown_conversation(manager: ContextManager) -> None:
    assert manager.get(uuid4()) is None


def test_get_hides_a_context_owned_by_another_principal(manager: ContextManager) -> None:
    identifier = uuid4()
    manager.for_conversation(identifier, user_id=uuid4())
    with pytest.raises(NotFoundError):
        manager.get(identifier, user_id=uuid4())


def test_get_allows_the_owner_and_an_unowned_reader(manager: ContextManager) -> None:
    identifier = uuid4()
    owner = uuid4()
    manager.for_conversation(identifier, user_id=owner)
    assert manager.get(identifier, user_id=owner) is not None
    assert manager.get(identifier) is not None


def test_require_raises_not_found_for_an_unknown_conversation(manager: ContextManager) -> None:
    with pytest.raises(NotFoundError) as caught:
        manager.require(uuid4())
    assert caught.value.code is ErrorCode.NOT_FOUND
    assert caught.value.http_status == 404
    assert caught.value.details["resource"] == "conversation"


def test_require_returns_a_known_context(manager: ContextManager) -> None:
    identifier = uuid4()
    created = manager.for_conversation(identifier)
    assert manager.require(identifier) is created


def test_drop_forgets_a_conversation(manager: ContextManager) -> None:
    identifier = uuid4()
    manager.for_conversation(identifier)
    assert manager.drop(identifier) is True
    assert manager.get(identifier) is None
    assert manager.drop(identifier) is False


def test_clear_forgets_every_conversation(manager: ContextManager) -> None:
    manager.for_conversation(uuid4())
    manager.for_conversation(uuid4())
    manager.clear()
    assert len(manager) == 0


def test_conversation_ids_lists_every_thread_sorted(manager: ContextManager) -> None:
    identifiers = [uuid4() for _ in range(3)]
    for identifier in identifiers:
        manager.for_conversation(identifier)
    assert manager.conversation_ids() == sorted(identifiers)


def test_contains_reports_a_held_conversation(manager: ContextManager) -> None:
    identifier = uuid4()
    manager.for_conversation(identifier)
    assert identifier in manager
    assert str(identifier) in manager
    assert uuid4() not in manager
    assert "not-a-uuid" not in manager
    assert 5 not in manager


def test_manager_propagates_its_cap_to_each_context() -> None:
    capped = ContextManager(max_entries=1)
    context = capped.for_conversation(uuid4())
    context.set("user", "a", 1)
    with pytest.raises(ConflictError):
        context.set("user", "b", 2)


def test_manager_refuses_a_non_positive_cap() -> None:
    with pytest.raises(ValueError, match="positive"):
        ContextManager(max_entries=0)


def test_manager_refuses_a_bool_cap() -> None:
    with pytest.raises(ValueError, match="positive"):
        ContextManager(max_entries=True)


def test_manager_repr_reports_sizes(manager: ContextManager) -> None:
    manager.for_conversation(uuid4())
    assert repr(manager) == f"ContextManager(conversations=1, max_entries={DEFAULT_MAX_ENTRIES})"


def test_manager_refuses_a_malformed_conversation_id(manager: ContextManager) -> None:
    with pytest.raises(InvalidInputError) as caught:
        manager.for_conversation("not-a-uuid")
    assert caught.value.http_status == 422


def test_manager_refuses_a_malformed_owner_id(manager: ContextManager) -> None:
    with pytest.raises(InvalidInputError):
        manager.for_conversation(uuid4(), user_id="not-a-uuid")
