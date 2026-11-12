from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, update

from agentos.agentic.agent_tools import AgentToolError, AgentToolset
from agentos.agentic.phases import PHASE_TOOLS, Phase
from agentos.agentic.prompts import PROJECT_TOOLS_GUIDANCE, extension_guidance_lines
from agentos.agentic.tool_deferral import (
    ALWAYS_LOADED_CODE_TOOLS,
    CODE_MODE_CORE_TOOLS,
    DEFERRED_TOOLS,
)
from agentos.agentic.tools.public_names import PUBLIC_NAMES, internal_name, public_name
from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.contracts import ApplicationNotFoundError
from agentos.conversations.chat import (
    BLOCKED_BY_HOOK_STATUS,
    BLOCKED_PROMPT_PLACEHOLDER,
    COMMAND_STATUS,
    ChatApplication,
    PostgresChatStore,
)
from agentos.persistence.postgres.schema import (
    conversation_compact_boundaries,
    conversation_messages,
    conversation_turns,
    conversations,
    metadata,
)
from agentos.projects import PostgresProjectStore


def _build_stores():
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    return engine, PostgresChatStore(engine), PostgresProjectStore(engine)


def _append_message(
    engine,
    *,
    conversation_id: str,
    user_id: str,
    sequence: int,
    role: str,
    content: str,
    status: str = "completed",
    turn_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    now = created_at or datetime.now(UTC)
    msg_id = f"msg_{conversation_id}_{sequence}"
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_messages).values(
                message_id=msg_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                user_id=user_id,
                role=role,
                content=content,
                sequence=sequence,
                status=status,
                retryable=False,
                created_at=now,
                updated_at=now,
            )
        )
    return msg_id


def test_search_conversations_and_around_sequence_enforce_strict_user_isolation(tmp_path: Path) -> None:
    engine, chat_store, project_store = _build_stores()
    proj_a = project_store.create(user_id="user-a", name="Projeto A", description=None)
    proj_b = project_store.create(user_id="user-b", name="Projeto B", description=None)

    conv_a = chat_store.create(
        user_id="user-a",
        message="Arquitetura de pagamentos Pix com idempotência",
        provider="openrouter",
        model_id="m",
        idempotency_key="idem-a1",
        project_id=proj_a.project_id,
    )
    conv_b = chat_store.create(
        user_id="user-b",
        message="Arquitetura de pagamentos Pix ultrassecreta do usuário B",
        provider="openrouter",
        model_id="m",
        idempotency_key="idem-b1",
        project_id=proj_b.project_id,
    )
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_compact_boundaries).values(
                boundary_id="bnd_b1",
                conversation_id=conv_b.conversation_id,
                user_id="user-b",
                turn_id=conv_b.turn_id,
                kind="compact",
                compact_trigger="auto",
                pre_tokens=2000,
                post_tokens=400,
                cumulative_dropped_tokens=1600,
                summary="Resumo secreto Pix do usuário B",
                through_sequence=2,
                created_at=datetime.now(UTC),
            )
        )

    hits_a = chat_store.search_conversations(
        "user-a",
        query="pagamentos Pix",
        project_id=proj_a.project_id,
        scope="all",
    )
    assert [item["conversation_id"] for item in hits_a] == [conv_a.conversation_id]

    hits_secret = chat_store.search_conversations(
        "user-a",
        query="ultrassecreta Resumo secreto",
        scope="all",
    )
    assert hits_secret == []

    with pytest.raises(ApplicationNotFoundError):
        chat_store.read_project_conversation(
            conv_b.conversation_id,
            "user-a",
            limit=5,
            around_sequence=1,
        )

    toolset_a = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-a"),
        project_store=project_store,
        chat_store=chat_store,
        project_user_id="user-a",
        project_id=proj_a.project_id,
        conversation_id="active-conv-a",
    )
    tool_res = toolset_a.search_conversations(query="pagamentos Pix", scope="all")
    parsed = json.loads(tool_res["content"])
    assert [r["conversation_id"] for r in parsed["results"]] == [conv_a.conversation_id]

    with pytest.raises(AgentToolError):
        toolset_a.read_project_conversation(
            conversation_id=conv_b.conversation_id,
            around_sequence=1,
            limit=5,
        )


def test_search_conversations_project_scope_all_scope_mode_and_exclude_active() -> None:
    engine, chat_store, project_store = _build_stores()
    proj_1 = project_store.create(user_id="u1", name="Proj 1", description=None)
    proj_2 = project_store.create(user_id="u1", name="Proj 2", description=None)

    c_proj1_chat = chat_store.create(
        user_id="u1",
        message="Migração do schema Alembic para vector embeddings",
        provider="openrouter",
        model_id="m",
        idempotency_key="k1",
        project_id=proj_1.project_id,
        code_mode="chat",
    )
    c_proj1_code = chat_store.create(
        user_id="u1",
        message="Implementação do worker Alembic vector embeddings em Python",
        provider="openrouter",
        model_id="m",
        idempotency_key="k2",
        project_id=proj_1.project_id,
        code_mode="code",
    )
    c_proj2_chat = chat_store.create(
        user_id="u1",
        message="Discussão externa sobre Alembic vector embeddings no projeto 2",
        provider="openrouter",
        model_id="m",
        idempotency_key="k3",
        project_id=proj_2.project_id,
        code_mode="chat",
    )
    c_standalone = chat_store.create(
        user_id="u1",
        message="Nota avulsa sobre Alembic vector embeddings fora de projeto",
        provider="openrouter",
        model_id="m",
        idempotency_key="k4",
        project_id=None,
        code_mode="chat",
    )

    # 1. scope="project" restricts to proj_1 and excludes active conversation
    proj_hits = chat_store.search_conversations(
        "u1",
        query="Alembic vector embeddings",
        project_id=proj_1.project_id,
        scope="project",
        exclude_conversation_id=c_proj1_chat.conversation_id,
    )
    assert [h["conversation_id"] for h in proj_hits] == [c_proj1_code.conversation_id]

    # 2. scope="all" searches across projects and standalone conversations for the same user
    all_hits = chat_store.search_conversations(
        "u1",
        query="Alembic vector embeddings",
        project_id=proj_1.project_id,
        scope="all",
        exclude_conversation_id=c_proj1_chat.conversation_id,
    )
    assert {h["conversation_id"] for h in all_hits} == {
        c_proj1_code.conversation_id,
        c_proj2_chat.conversation_id,
        c_standalone.conversation_id,
    }

    # 3. mode filter restricts to mode="code"
    code_only = chat_store.search_conversations(
        "u1",
        query="Alembic vector embeddings",
        project_id=proj_1.project_id,
        scope="all",
        mode="code",
    )
    assert [h["conversation_id"] for h in code_only] == [c_proj1_code.conversation_id]
    assert code_only[0]["mode"] == "code"

    # 4. Explicit scope="project" with project_id=None returns [] (never leaks across all user conversations)
    empty_project_hits = chat_store.search_conversations(
        "u1",
        query="Alembic vector embeddings",
        project_id=None,
        scope="project",
    )
    assert empty_project_hits == []


def test_search_conversations_excludes_blocked_messages_and_turns() -> None:
    engine, chat_store, _ = _build_stores()
    conv = chat_store.create(
        user_id="u1",
        message="Conversa inicial limpa sobre gráficos",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-clean",
    )
    # Add a blocked user message at sequence 3 and its paired assistant message at sequence 4
    blocked_user_msg_id = _append_message(
        engine,
        conversation_id=conv.conversation_id,
        user_id="u1",
        sequence=3,
        role="user",
        content="TermoProibidoHookX99 que foi bloqueado pelo hook",
        status=BLOCKED_BY_HOOK_STATUS,
        turn_id="turn_blocked_1",
    )
    blocked_assistant_msg_id = _append_message(
        engine,
        conversation_id=conv.conversation_id,
        user_id="u1",
        sequence=4,
        role="assistant",
        content="RespostaDoTurnoBloqueadoY88 que não deve aparecer nunca",
        status="completed",
        turn_id="turn_blocked_1",
    )
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_turns).values(
                turn_id="turn_blocked_1",
                conversation_id=conv.conversation_id,
                user_id="u1",
                user_message_id=blocked_user_msg_id,
                assistant_message_id=blocked_assistant_msg_id,
                execution_id="exec_blocked_1",
                provider="openrouter",
                model_id="m",
                idempotency_key="k-blocked",
                state="completed",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    # Also add a command turn message (status=COMMAND_STATUS) at sequence 5
    _append_message(
        engine,
        conversation_id=conv.conversation_id,
        user_id="u1",
        sequence=5,
        role="user",
        content="/compact ComandoInternoCompactZ77",
        status=COMMAND_STATUS,
    )
    # Add a valid message at sequence 6
    _append_message(
        engine,
        conversation_id=conv.conversation_id,
        user_id="u1",
        sequence=6,
        role="assistant",
        content="Explicação válida sobre gráficos D3 e Canvas.",
        status="completed",
    )

    assert chat_store.search_conversations("u1", query="TermoProibidoHookX99", scope="all") == []
    assert chat_store.search_conversations("u1", query="RespostaDoTurnoBloqueadoY88", scope="all") == []
    assert chat_store.search_conversations("u1", query="ComandoInternoCompactZ77", scope="all") == []

    valid_hits = chat_store.search_conversations("u1", query="gráficos D3 Canvas", scope="all")
    assert len(valid_hits) == 1
    assert valid_hits[0]["matched_sequence"] == 6
    assert "TermoProibidoHookX99" not in valid_hits[0]["snippet"]
    assert "RespostaDoTurnoBloqueadoY88" not in valid_hits[0]["snippet"]

    # Also verify read_project_conversation(around_sequence=4) excludes sequences 3, 4, 5
    window = chat_store.read_project_conversation(
        conv.conversation_id,
        "u1",
        limit=10,
        around_sequence=4,
    )
    returned_sequences = [m["sequence"] for m in window["messages"]]
    assert 3 not in returned_sequences
    assert 4 not in returned_sequences
    assert 5 not in returned_sequences
    assert 6 in returned_sequences


def test_search_conversations_matches_title_messages_and_compact_summary_with_sanitized_snippet() -> None:
    engine, chat_store, _ = _build_stores()

    # 1. Conversation matching only in title
    conv_title = chat_store.create(
        user_id="u1",
        message="Vamos começar o trabalho de hoje",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-title",
    )
    chat_store.rename(conv_title.conversation_id, "u1", " migração-exclusiva-scylla-db ")

    title_hits = chat_store.search_conversations("u1", query="scylla-db", scope="all")
    assert len(title_hits) == 1
    assert title_hits[0]["conversation_id"] == conv_title.conversation_id
    assert title_hits[0]["matched_source"] == "title"
    assert title_hits[0]["matched_sequence"] is None
    assert "scylla-db" in title_hits[0]["snippet"]

    # 2. Conversation matching in compact boundary summary
    conv_summary = chat_store.create(
        user_id="u1",
        message="Mensagem antiga genérica",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-sum",
    )
    with engine.begin() as conn:
        conn.execute(
            insert(conversation_compact_boundaries).values(
                boundary_id="bnd_sum_1",
                conversation_id=conv_summary.conversation_id,
                user_id="u1",
                turn_id=conv_summary.turn_id,
                kind="compact",
                compact_trigger="auto",
                pre_tokens=5000,
                post_tokens=800,
                cumulative_dropped_tokens=4200,
                summary=(
                    "<system-reminder>Internal instructions never shown</system-reminder>\n"
                    "Decidimos adotar o protocolo RaftConsensusV2 no cluster primário com quorum de 5 nós."
                ),
                through_sequence=18,
                created_at=datetime.now(UTC),
            )
        )

    sum_hits = chat_store.search_conversations("u1", query="RaftConsensusV2 quorum", scope="all")
    assert len(sum_hits) == 1
    assert sum_hits[0]["conversation_id"] == conv_summary.conversation_id
    assert sum_hits[0]["matched_source"] == "summary"
    assert sum_hits[0]["matched_sequence"] == 18
    assert "<system-reminder>" not in sum_hits[0]["snippet"]
    assert "RaftConsensusV2" in sum_hits[0]["snippet"]

    # 3. Conversation matching in a long message with <system-reminder> and secret token
    conv_msg = chat_store.create(
        user_id="u1",
        message="Início da conversa",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-msg",
    )
    prefix_padding = "Texto introdutório de contexto geral sobre o módulo. " * 10
    suffix_padding = " Texto posterior com detalhes adicionais de encerramento." * 10
    long_content = (
        "<system-reminder>Segredo interno do sistema</system-reminder>\n"
        f"{prefix_padding}"
        "Aqui configuramos o indexador PageRankPersonalizado usando a chave sk-proj-1234567890abcdefghijklmnop "
        f"para calcular centralidade.{suffix_padding}"
    )
    _append_message(
        engine,
        conversation_id=conv_msg.conversation_id,
        user_id="u1",
        sequence=12,
        role="assistant",
        content=long_content,
    )

    msg_hits = chat_store.search_conversations("u1", query="PageRankPersonalizado centralidade", scope="all")
    assert len(msg_hits) == 1
    hit = msg_hits[0]
    assert hit["conversation_id"] == conv_msg.conversation_id
    assert hit["matched_source"] == "message"
    assert hit["matched_sequence"] == 12
    assert "PageRankPersonalizado" in hit["snippet"]
    assert "<system-reminder>" not in hit["snippet"]
    assert "Segredo interno do sistema" not in hit["snippet"]
    assert "sk-proj-1234567890abcdefghijklmnop" not in hit["snippet"]
    assert len(hit["snippet"]) <= 360


def test_search_conversations_ranking_coverage_and_recency() -> None:
    engine, chat_store, _ = _build_stores()
    now = datetime.now(UTC)

    # Conv 1: only 1 of 3 query terms
    c_partial = chat_store.create(
        user_id="u1",
        message="Usamos Redis para cache simples.",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-rank-1",
    )
    # Conv 2: all 3 query terms, older
    c_older_full = chat_store.create(
        user_id="u1",
        message="Configuração de Redis Streams com consumer groups para fila distribuída.",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-rank-2",
    )
    # Conv 3: all 3 query terms, newer
    c_newer_full = chat_store.create(
        user_id="u1",
        message="Configuração de Redis Streams com consumer groups para fila distribuída.",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-rank-3",
    )

    with engine.begin() as conn:
        conn.execute(
            update(conversations)
            .where(conversations.c.conversation_id == c_older_full.conversation_id)
            .values(updated_at=now - timedelta(days=30))
        )
        conn.execute(
            update(conversations)
            .where(conversations.c.conversation_id == c_newer_full.conversation_id)
            .values(updated_at=now)
        )

    hits = chat_store.search_conversations(
        "u1",
        query="Redis Streams consumer",
        scope="all",
    )
    assert [h["conversation_id"] for h in hits] == [
        c_newer_full.conversation_id,
        c_older_full.conversation_id,
        c_partial.conversation_id,
    ]


def test_read_project_conversation_around_sequence_returns_centered_window_in_long_conversation(
    tmp_path: Path,
) -> None:
    engine, chat_store, project_store = _build_stores()
    proj = project_store.create(user_id="u1", name="Long Conv Project", description=None)
    conv = chat_store.create(
        user_id="u1",
        message="Mensagem 1 inicial",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-long-1",
        project_id=proj.project_id,
    )
    # Create conversation messages from sequence 3 to 40 (note create() already created sequence 1 and 2)
    with engine.begin() as conn:
        conn.execute(
            update(conversation_messages)
            .where(
                conversation_messages.c.conversation_id == conv.conversation_id,
                conversation_messages.c.sequence == 2,
            )
            .values(content="Mensagem 2 do assistente", status="completed")
        )
    for seq in range(3, 41):
        role = "user" if seq % 2 == 1 else "assistant"
        marker = "DECISAO_CRITICA_SEQ_25_HNSW" if seq == 25 else f"Conteúdo normal da mensagem {seq}"
        _append_message(
            engine,
            conversation_id=conv.conversation_id,
            user_id="u1",
            sequence=seq,
            role=role,
            content=marker,
        )

    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-long"),
        project_store=project_store,
        chat_store=chat_store,
        project_user_id="u1",
        project_id=proj.project_id,
        conversation_id="other-active-session",
    )

    search_out = toolset.search_conversations(query="DECISAO_CRITICA_SEQ_25_HNSW")
    search_data = json.loads(search_out["content"])
    assert len(search_data["results"]) == 1
    matched_seq = search_data["results"][0]["matched_sequence"]
    assert matched_seq == 25

    read_out = toolset.read_project_conversation(
        conversation_id=conv.conversation_id,
        around_sequence=matched_seq,
        limit=6,
    )
    read_data = json.loads(read_out["content"])
    messages = read_data["messages"]
    assert len(messages) == 6
    sequences = [m["sequence"] for m in messages]
    assert sequences == [22, 23, 24, 25, 26, 27]
    assert any("DECISAO_CRITICA_SEQ_25_HNSW" in m["content"] for m in messages)

    # Without around_sequence, returns the last 6 messages (35..40)
    tail_out = toolset.read_project_conversation(
        conversation_id=conv.conversation_id,
        limit=6,
    )
    tail_data = json.loads(tail_out["content"])
    assert [m["sequence"] for m in tail_data["messages"]] == [35, 36, 37, 38, 39, 40]


def test_search_conversations_public_name_deferral_phases_and_prompts(tmp_path: Path) -> None:
    assert PUBLIC_NAMES.get("search_conversations") == "SearchConversations"
    assert public_name("search_conversations") == "SearchConversations"
    assert internal_name("SearchConversations") == "search_conversations"
    assert internal_name("searchconversations") == "search_conversations"

    assert "search_conversations" in CODE_MODE_CORE_TOOLS
    assert "search_conversations" not in ALWAYS_LOADED_CODE_TOOLS
    assert "search_conversations" in DEFERRED_TOOLS
    assert "read_project_conversation" in DEFERRED_TOOLS

    assert "search_conversations" in PHASE_TOOLS[Phase.ORIENT]
    assert "search_conversations" in PHASE_TOOLS[Phase.PLAN]
    assert "search_conversations" in PHASE_TOOLS[Phase.EXECUTE]

    assert "search_conversations" in PROJECT_TOOLS_GUIDANCE
    assert "around_sequence" in PROJECT_TOOLS_GUIDANCE
    assert any("search_conversations" in line for line in extension_guidance_lines(["search_conversations"]))

    _, chat_store, project_store = _build_stores()
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-def"),
        project_store=project_store,
        chat_store=chat_store,
        project_user_id="u1",
        code_mode_active=True,
    )
    assert "search_conversations" in toolset.deferred_tool_names
    app = ChatApplication(chat_store)
    assert hasattr(app, "search_conversations")


def test_c1_has_external_tool_calls_scans_entire_conversation_beyond_limit_20() -> None:
    _engine, chat_store, _ = _build_stores()
    conv = chat_store.create(
        user_id="u-ext",
        message="Iniciando tarefa longa",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-ext-1",
    )
    turn = chat_store.claim(conv.turn_id)
    assert turn is not None
    assert chat_store.has_external_tool_calls(conv.conversation_id) is False

    # Record an untrusted external tool call at sequence 1
    chat_store.record_tool_call(
        turn,
        tool_name="WebFetch",
        arguments={"url": "https://untrusted.example/page"},
        status="succeeded",
        summary="Fetched external page",
    )
    assert chat_store.has_external_tool_calls(conv.conversation_id) is True

    # Record 30 safe local tool calls after sequence 1 (pushing WebFetch out of tool_ledger(limit=20))
    for idx in range(30):
        chat_store.record_tool_call(
            turn,
            tool_name="read_file",
            arguments={"path": f"file_{idx}.py"},
            status="succeeded",
            summary=f"Read file_{idx}.py",
        )
    ledger_20 = chat_store.tool_ledger(turn, limit=20)
    assert all(item["tool_name"] == "read_file" for item in ledger_20)
    # has_external_tool_calls still detects sequence 1 without LIMIT 20 truncation!
    assert chat_store.has_external_tool_calls(conv.conversation_id) is True
    assert ChatApplication(chat_store).has_external_tool_calls(conv.conversation_id) is True

    # Also test mcp__* prefix detection
    conv_mcp = chat_store.create(
        user_id="u-ext",
        message="Usando servidor MCP",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-ext-2",
    )
    turn_mcp = chat_store.claim(conv_mcp.turn_id)
    assert turn_mcp is not None
    chat_store.record_tool_call(
        turn_mcp,
        tool_name="mcp__github__get_issue",
        arguments={"id": 1},
        status="succeeded",
        summary="Read GitHub issue",
    )
    assert chat_store.has_external_tool_calls(conv_mcp.conversation_id) is True


def test_h5_and_m2_scope_project_without_project_id_friend_exclusion_and_sqlite_batching(tmp_path: Path) -> None:
    engine, chat_store, project_store = _build_stores()

    # Create a Chat conversation and a Friend conversation with a shared keyword
    c_chat = chat_store.create(
        user_id="u-h5",
        message="SegredoTecnicoAlpha99 discutido no chat normal",
        provider="openrouter",
        model_id="m",
        idempotency_key="k-h5-chat",
        code_mode="chat",
    )
    friend_info = chat_store.ensure_friend_conversation("u-h5")
    friend_cid = str(friend_info["conversation_id"])
    _append_message(
        engine,
        conversation_id=friend_cid,
        user_id="u-h5",
        sequence=1,
        role="user",
        content="SegredoTecnicoAlpha99 desabafo pessoal no Orin Friend",
    )

    # 1. Default search (mode=None, include_friend=False) excludes mode='friend'
    default_hits = chat_store.search_conversations("u-h5", query="SegredoTecnicoAlpha99", scope="all")
    assert [h["conversation_id"] for h in default_hits] == [c_chat.conversation_id]

    # 2. Explicit mode="friend" or include_friend=True or caller is friend conversation includes mode='friend'
    friend_mode_hits = chat_store.search_conversations(
        "u-h5", query="SegredoTecnicoAlpha99", scope="all", mode="friend"
    )
    assert [h["conversation_id"] for h in friend_mode_hits] == [friend_cid]

    inc_friend_hits = chat_store.search_conversations(
        "u-h5", query="SegredoTecnicoAlpha99", scope="all", include_friend=True
    )
    assert {h["conversation_id"] for h in inc_friend_hits} == {c_chat.conversation_id, friend_cid}

    # Toolset outside a project: scope=None defaults to "all", explicit scope="project" returns []
    toolset_no_proj = AgentToolset(
        ConversationWorkspace(tmp_path, "ws-no-proj"),
        project_store=project_store,
        chat_store=chat_store,
        project_user_id="u-h5",
        project_id=None,
        conversation_id=c_chat.conversation_id,
    )
    explicit_proj_res = json.loads(
        toolset_no_proj.search_conversations(query="SegredoTecnicoAlpha99", scope="project")["content"]
    )
    assert explicit_proj_res["scope"] == "project"
    assert explicit_proj_res["results"] == []

    # 3. M2: _blocked_message_ids and search_conversations handle > 450 conversations without SQLite variable overflow
    now = datetime.now(UTC)
    bulk_convs = [
        {
            "conversation_id": f"conv_bulk_{i}",
            "user_id": "u-bulk",
            "title": f"Bulk conv {i}",
            "provider": "openrouter",
            "model_id": "m",
            "project_id": None,
            "mode": "chat",
            "permission_mode": "ask_permissions",
            "state": "completed",
            "created_at": now - timedelta(seconds=i),
            "updated_at": now - timedelta(seconds=i),
        }
        for i in range(520)
    ]
    bulk_msgs = [
        {
            "message_id": f"msg_bulk_{i}",
            "conversation_id": f"conv_bulk_{i}",
            "turn_id": None,
            "user_id": "u-bulk",
            "role": "user",
            "content": "TermoEmLoteMassivo520" if i == 10 else "outro texto",
            "sequence": 1,
            "status": BLOCKED_BY_HOOK_STATUS if i >= 20 else "completed",
            "retryable": False,
            "created_at": now,
            "updated_at": now,
        }
        for i in range(520)
    ]
    with engine.begin() as conn:
        conn.execute(insert(conversations), bulk_convs)
        conn.execute(insert(conversation_messages), bulk_msgs)

    bulk_hits = chat_store.search_conversations("u-bulk", query="TermoEmLoteMassivo520", scope="all")
    assert [h["conversation_id"] for h in bulk_hits] == ["conv_bulk_10"]

