"""Evolutionary Glass-Box Personality Engine (`user_friend_personas` <-> `~/.orin/friend/SOUL.md`).

Maintains a 7-trait bounded personality vector per user with anti-sycophancy floors
(`candor >= 0.50`), per-session delta caps (`|delta| <= 0.03`), deterministic PT-BR
prompt rubric compilation, bidirectional `SOUL.md` synchronization, and taint-gated
post-session reflection (`W3-R27`).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from agentos.memory.security import validate_memory_content
from agentos.persistence.postgres.agent_memory import FRIEND_MEMORY_KINDS, PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, user_friend_personas
from agentos.settings.paths import orin_user_dir


DEFAULT_TRAITS: dict[str, float] = {
    "warmth": 0.65,
    "humor": 0.50,
    "sarcasm": 0.25,
    "formality": 0.35,
    "verbosity": 0.40,
    "candor": 0.70,
    "energy": 0.55,
}

TRAIT_BOUNDS: dict[str, tuple[float, float]] = {
    "warmth": (0.30, 0.95),
    "humor": (0.10, 0.90),
    "sarcasm": (0.00, 0.75),
    "formality": (0.05, 0.85),
    "verbosity": (0.15, 0.80),
    "candor": (0.50, 0.95),
    "energy": (0.20, 0.90),
}

MAX_TRAIT_DELTA_PER_SESSION = 0.03
VALID_BOND_STAGES = ("acquaintance", "buddy", "close_friend", "confidant")
MAX_STYLE_NOTES = 20
MAX_STYLE_NOTE_CHARS = 280

_PROMPT_INJECTION_RE = re.compile(
    r"(?ix)(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|policy|prompts|rules)|"
    r"<\s*/?\s*system\s*>|system\s+prompt|developer\s+message|rm\s+-rf)"
)

FRIEND_REFLECTION_INSTRUCTION = (
    "Analise o trecho de falas do usuário abaixo e extraia apenas sinais legítimos de evolução de vínculo "
    "e memórias afetivas duradouras (episodic, inside_joke, milestone, emotional). "
    "Responda estritamente com um objeto JSON no formato: "
    '{"mood": "string", "affinity_delta": 0.0, "trait_deltas": {"warmth": 0.0}, '
    '"style_notes": ["string"], "memories": [{"fact": "string", "kind": "episodic", '
    '"emotional_valence": 0.5, "salience": 0.7, "tags": ["tag"]}]}.'
)


def _clamp_trait(name: str, value: float) -> float:
    low, high = TRAIT_BOUNDS.get(name, (0.0, 1.0))
    return round(max(low, min(high, float(value))), 4)


def normalize_traits(raw: Mapping[str, Any] | None) -> dict[str, float]:
    merged: dict[str, float] = {}
    source = raw if isinstance(raw, Mapping) else {}
    for key, default_val in DEFAULT_TRAITS.items():
        try:
            val = float(source.get(key, default_val))
        except (TypeError, ValueError):
            val = default_val
        merged[key] = _clamp_trait(key, val)
    return merged


def validate_style_note(note: str) -> str:
    cleaned = " ".join(str(note or "").split())[:MAX_STYLE_NOTE_CHARS]
    validate_memory_content(cleaned)
    if _PROMPT_INJECTION_RE.search(cleaned):
        raise ValueError("style note violates the memory data policy")
    return cleaned


def bond_stage_for_affinity(affinity_score: float, current_stage: str = "acquaintance") -> str:
    score = max(0.0, min(100.0, float(affinity_score)))
    if score >= 80.0:
        computed = "confidant"
    elif score >= 50.0:
        computed = "close_friend"
    elif score >= 20.0:
        computed = "buddy"
    else:
        computed = "acquaintance"
    if current_stage in VALID_BOND_STAGES:
        if VALID_BOND_STAGES.index(current_stage) > VALID_BOND_STAGES.index(computed):
            return current_stage
    return computed


def compile_persona_rubric(
    traits: Mapping[str, float],
    bond_stage: str,
    style_notes: Sequence[str] = (),
) -> str:
    """Compile a deterministic, cache-safe PT-BR personality rubric for system prompt injection."""
    norm = normalize_traits(traits)
    stage_labels = {
        "acquaintance": "Conhecido recente — cordial, atento e construindo rapport sem forçar intimidade.",
        "buddy": "Parceiro de jornada (Buddy) — tom camarada, fluido, conhece o ritmo de trabalho do usuário.",
        "close_friend": "Amigo próximo — cumplicidade alta, referências naturais a momentos vividos e humor afiado.",
        "confidant": "Confidente de longa data — sintonia profunda, lealdade total e franqueza cirúrgica.",
    }
    stage_desc = stage_labels.get(bond_stage, stage_labels["acquaintance"])

    warmth_desc = (
        "caloroso e acolhedor" if norm["warmth"] >= 0.70
        else ("equilibrado e empático" if norm["warmth"] >= 0.45 else "objetivo e contido")
    )
    humor_desc = (
        "humor frequente e espirituoso" if norm["humor"] >= 0.65
        else ("humor leve quando oportuno" if norm["humor"] >= 0.35 else "postura séria e focada")
    )
    sarcasm_desc = (
        "ironia fina e provocação amigável" if norm["sarcasm"] >= 0.50
        else ("toque sutil de ironia ocasional" if norm["sarcasm"] >= 0.20 else "zero sarcasmo, literal e gentil")
    )
    formality_desc = (
        "tratamento respeitoso/formal" if norm["formality"] >= 0.60
        else ("coloquial técnico natural" if norm["formality"] >= 0.25 else "descontraído e direto")
    )
    verbosity_desc = (
        "explicações detalhadas e conversacionais" if norm["verbosity"] >= 0.60
        else ("respostas concisas e ritmadas para voz" if norm["verbosity"] >= 0.30 else "ultra-enxuto, direto ao ponto")
    )
    candor_desc = (
        "franqueza máxima: discorda com clareza quando vê um erro e jamais bajula"
        if norm["candor"] >= 0.75
        else "honestidade direta sem adulação (piso anti-bajulação ativo)"
    )
    energy_desc = (
        "energia alta, entusiasmada e dinâmica" if norm["energy"] >= 0.65
        else ("ritmo calmo, firme e seguro" if norm["energy"] >= 0.35 else "sereno e pausado")
    )

    lines = [
        "### Rubrica de Personalidade do Orin Friend (Caixa de Vidro)",
        f"- **Estágio do Vínculo (`{bond_stage}`):** {stage_desc}",
        f"- **Calor Humano (`warmth={norm['warmth']:.2f}`):** {warmth_desc}.",
        f"- **Humor (`humor={norm['humor']:.2f}`) & Ironia (`sarcasm={norm['sarcasm']:.2f}`):** {humor_desc}; {sarcasm_desc}.",
        f"- **Registro (`formality={norm['formality']:.2f}`) & Ritmo (`verbosity={norm['verbosity']:.2f}`):** {formality_desc}; {verbosity_desc}.",
        f"- **Franqueza (`candor={norm['candor']:.2f}`):** {candor_desc}.",
        f"- **Energia (`energy={norm['energy']:.2f}`):** {energy_desc}.",
    ]
    if style_notes:
        lines.append("- **Notas de Estilo Aprendidas:**")
        for note in style_notes[:MAX_STYLE_NOTES]:
            lines.append(f"  - {note}")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class FriendPersonaState:
    user_id: str
    version: int = 1
    bond_stage: str = "acquaintance"
    affinity_score: float = 10.0
    current_mood: str = "sereno"
    traits: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TRAITS))
    style_notes: list[str] = field(default_factory=list)
    compiled_rubric: str = ""
    interaction_count: int = 0
    soul_sha256: str = ""
    last_reflected_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "version": self.version,
            "bond_stage": self.bond_stage,
            "affinity_score": round(float(self.affinity_score), 2),
            "current_mood": self.current_mood,
            "traits": dict(self.traits),
            "style_notes": list(self.style_notes),
            "compiled_rubric": self.compiled_rubric,
            "interaction_count": self.interaction_count,
            "soul_sha256": self.soul_sha256,
            "last_reflected_at": self.last_reflected_at.isoformat() if self.last_reflected_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def render_soul_markdown(state: FriendPersonaState) -> str:
    traits_lines = "\n".join(f"  {k}: {state.traits[k]:.2f}" for k in DEFAULT_TRAITS)
    notes_lines = (
        "\n".join(f"- {note}" for note in state.style_notes)
        if state.style_notes
        else "- Sem notas adicionais registradas ainda."
    )
    return (
        "---\n"
        f"user_id: {state.user_id}\n"
        f"version: {state.version}\n"
        f"bond_stage: {state.bond_stage}\n"
        f"affinity_score: {state.affinity_score:.1f}\n"
        f"current_mood: {state.current_mood}\n"
        "traits:\n"
        f"{traits_lines}\n"
        "---\n"
        "# Orin Friend — SOUL.md\n\n"
        "## Notas de Estilo e Convivência\n"
        f"{notes_lines}\n"
    )


def parse_soul_markdown(markdown_text: str) -> dict[str, Any]:
    """Parse YAML frontmatter and Markdown style notes from `SOUL.md` without external dependencies."""
    text_val = str(markdown_text or "")
    frontmatter = ""
    body = text_val
    if text_val.startswith("---"):
        parts = text_val.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2]

    parsed: dict[str, Any] = {"traits": {}, "style_notes": []}
    in_traits = False
    for raw_line in frontmatter.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        if in_traits and (raw_line.startswith("  ") or raw_line.startswith("\t")):
            if ":" in raw_line:
                k, v = raw_line.strip().split(":", 1)
                k = k.strip()
                if k in DEFAULT_TRAITS:
                    try:
                        parsed["traits"][k] = float(v.strip())
                    except ValueError:
                        pass
            continue
        in_traits = False
        if ":" in raw_line:
            key, val = raw_line.split(":", 1)
            key = key.strip()
            val = val.strip().strip("\"'")
            if key == "traits":
                in_traits = True
            elif key in {"bond_stage", "current_mood", "user_id"}:
                parsed[key] = val
            elif key == "affinity_score":
                try:
                    parsed["affinity_score"] = float(val)
                except ValueError:
                    pass
            elif key == "version":
                try:
                    parsed["version"] = int(val)
                except ValueError:
                    pass

    notes: list[str] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            candidate = stripped[2:].strip()
            if candidate and candidate != "Sem notas adicionais registradas ainda.":
                try:
                    cleaned = validate_style_note(candidate)
                    if cleaned not in notes:
                        notes.append(cleaned)
                except ValueError:
                    continue
    parsed["style_notes"] = notes[:MAX_STYLE_NOTES]
    return parsed


class FriendPersonaStore:
    def __init__(
        self,
        engine: Engine,
        *,
        soul_dir: Path | str | None = None,
        soul_path: Path | str | None = None,
    ) -> None:
        self._engine = engine
        self._soul_dir = Path(soul_dir) if soul_dir is not None else None
        self._soul_path = Path(soul_path) if soul_path is not None else None
        try:
            user_friend_personas.create(self._engine, checkfirst=True)
        except Exception:
            pass

    def soul_file_path(self, user_id: str | None = None) -> Path:
        _ = user_id
        if self._soul_path is not None:
            return self._soul_path
        if self._soul_dir is not None:
            return self._soul_dir / "SOUL.md"
        return orin_user_dir() / "friend" / "SOUL.md"

    def _has_existing_global_memories(self, user_id: str) -> bool:
        try:
            with self._engine.connect() as conn:
                count = conn.execute(
                    select(func.count())
                    .select_from(agent_memories)
                    .where(
                        agent_memories.c.user_id == user_id,
                        agent_memories.c.scope_type.in_(("user", "project")),
                        agent_memories.c.superseded_by.is_(None),
                    )
                ).scalar_one()
                return int(count or 0) > 0
        except Exception:
            return False

    def get(self, user_id: str) -> FriendPersonaState:
        return self.get_or_create(user_id)

    def get_or_create(self, user_id: str) -> FriendPersonaState:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(user_friend_personas).where(user_friend_personas.c.user_id == user_id)
            ).mappings().first()
        if row is not None:
            return self._row_to_state(row)

        now = datetime.now(UTC)
        has_memories = self._has_existing_global_memories(user_id)
        bond_stage = "buddy" if has_memories else "acquaintance"
        affinity_score = 25.0 if has_memories else 10.0
        traits = dict(DEFAULT_TRAITS)
        style_notes: list[str] = []
        rubric = compile_persona_rubric(traits, bond_stage, style_notes)

        provisional = FriendPersonaState(
            user_id=user_id,
            version=1,
            bond_stage=bond_stage,
            affinity_score=affinity_score,
            current_mood="sereno",
            traits=traits,
            style_notes=style_notes,
            compiled_rubric=rubric,
            interaction_count=0,
            soul_sha256="",
            last_reflected_at=None,
            updated_at=now,
        )
        soul_sha = self._write_soul_file(provisional)
        final_state = FriendPersonaState(
            user_id=provisional.user_id,
            version=provisional.version,
            bond_stage=provisional.bond_stage,
            affinity_score=provisional.affinity_score,
            current_mood=provisional.current_mood,
            traits=provisional.traits,
            style_notes=provisional.style_notes,
            compiled_rubric=provisional.compiled_rubric,
            interaction_count=provisional.interaction_count,
            soul_sha256=soul_sha,
            last_reflected_at=provisional.last_reflected_at,
            updated_at=now,
        )

        with self._engine.begin() as conn:
            existing = conn.execute(
                select(user_friend_personas.c.user_id).where(user_friend_personas.c.user_id == user_id)
            ).scalar()
            if existing is None:
                conn.execute(
                    insert(user_friend_personas).values(
                        user_id=user_id,
                        version=final_state.version,
                        bond_stage=final_state.bond_stage,
                        affinity_score=final_state.affinity_score,
                        current_mood=final_state.current_mood,
                        traits_json=final_state.traits,
                        style_notes_json=final_state.style_notes,
                        compiled_rubric=final_state.compiled_rubric,
                        interaction_count=final_state.interaction_count,
                        soul_sha256=final_state.soul_sha256,
                        last_reflected_at=final_state.last_reflected_at,
                        updated_at=now,
                    )
                )
        return final_state

    def apply_reflection_deltas(
        self,
        user_id: str,
        trait_deltas: Mapping[str, float] | None = None,
        mood: str | None = None,
        new_style_notes: Sequence[str] | None = None,
        affinity_delta: float = 0.0,
        *,
        damping: float = 1.0,
    ) -> FriendPersonaState:
        """Apply bounded EMA reflection deltas (`|delta| <= 0.03` per session) and persist."""
        current = self.get_or_create(user_id)
        validated_notes: list[str] = list(current.style_notes)
        if new_style_notes:
            for raw_note in new_style_notes:
                cleaned = validate_style_note(raw_note)
                if cleaned not in validated_notes:
                    validated_notes.append(cleaned)
        validated_notes = validated_notes[-MAX_STYLE_NOTES:]

        updated_traits = dict(current.traits)
        if trait_deltas:
            for name, raw_delta in trait_deltas.items():
                if name not in DEFAULT_TRAITS:
                    continue
                damped = float(raw_delta) * float(damping)
                clamped_delta = max(-MAX_TRAIT_DELTA_PER_SESSION, min(MAX_TRAIT_DELTA_PER_SESSION, damped))
                updated_traits[name] = _clamp_trait(name, updated_traits[name] + clamped_delta)

        clamped_affinity_delta = max(-5.0, min(10.0, float(affinity_delta)))
        new_affinity = round(max(0.0, min(100.0, current.affinity_score + clamped_affinity_delta)), 2)
        new_stage = bond_stage_for_affinity(new_affinity, current.bond_stage)
        new_mood = (str(mood).strip()[:64] if mood and str(mood).strip() else current.current_mood)
        rubric = compile_persona_rubric(updated_traits, new_stage, validated_notes)
        now = datetime.now(UTC)

        next_state = FriendPersonaState(
            user_id=user_id,
            version=current.version + 1,
            bond_stage=new_stage,
            affinity_score=new_affinity,
            current_mood=new_mood,
            traits=updated_traits,
            style_notes=validated_notes,
            compiled_rubric=rubric,
            interaction_count=current.interaction_count + 1,
            soul_sha256="",
            last_reflected_at=now,
            updated_at=now,
        )
        return self._persist_and_mirror(next_state)

    def update_persona(
        self,
        user_id: str,
        *,
        traits: Mapping[str, float] | None = None,
        style_notes: Sequence[str] | None = None,
        current_mood: str | None = None,
        bond_stage: str | None = None,
        affinity_score: float | None = None,
    ) -> FriendPersonaState:
        """Direct Glass-Box update initiated by the user in UI settings."""
        current = self.get_or_create(user_id)
        updated_traits = dict(current.traits)
        if traits:
            for k, v in traits.items():
                if k in DEFAULT_TRAITS:
                    updated_traits[k] = _clamp_trait(k, float(v))

        if style_notes is not None:
            validated_notes = []
            for note in style_notes:
                cleaned = validate_style_note(note)
                if cleaned not in validated_notes:
                    validated_notes.append(cleaned)
            validated_notes = validated_notes[:MAX_STYLE_NOTES]
        else:
            validated_notes = list(current.style_notes)

        new_affinity = (
            round(max(0.0, min(100.0, float(affinity_score))), 2)
            if affinity_score is not None
            else current.affinity_score
        )
        if bond_stage is not None and bond_stage in VALID_BOND_STAGES:
            new_stage = bond_stage
        else:
            new_stage = bond_stage_for_affinity(new_affinity, current.bond_stage)
        new_mood = str(current_mood).strip()[:64] if current_mood and str(current_mood).strip() else current.current_mood
        rubric = compile_persona_rubric(updated_traits, new_stage, validated_notes)
        now = datetime.now(UTC)

        next_state = FriendPersonaState(
            user_id=user_id,
            version=current.version + 1,
            bond_stage=new_stage,
            affinity_score=new_affinity,
            current_mood=new_mood,
            traits=updated_traits,
            style_notes=validated_notes,
            compiled_rubric=rubric,
            interaction_count=current.interaction_count,
            soul_sha256="",
            last_reflected_at=current.last_reflected_at,
            updated_at=now,
        )
        return self._persist_and_mirror(next_state)

    def sync_from_disk_if_modified(self, user_id: str | None = None) -> FriendPersonaState:
        """Check if `SOUL.md` on disk was edited externally; if so, parse, clamp, and update DB."""
        resolved_user_id = user_id or self._default_user_id()
        current = self.get_or_create(resolved_user_id)
        path = self.soul_file_path(resolved_user_id)
        if not path.exists():
            return self._persist_and_mirror(current)

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError:
            return current

        disk_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        if disk_sha == current.soul_sha256:
            return current

        parsed = parse_soul_markdown(raw_text)
        merged_traits = dict(current.traits)
        for k, v in (parsed.get("traits") or {}).items():
            if k in DEFAULT_TRAITS:
                merged_traits[k] = _clamp_trait(k, float(v))

        raw_stage = str(parsed.get("bond_stage") or current.bond_stage).strip()
        new_stage = raw_stage if raw_stage in VALID_BOND_STAGES else current.bond_stage
        new_affinity = (
            round(max(0.0, min(100.0, float(parsed["affinity_score"]))), 2)
            if "affinity_score" in parsed
            else current.affinity_score
        )
        new_mood = str(parsed.get("current_mood") or current.current_mood).strip()[:64] or current.current_mood
        new_notes = list(parsed.get("style_notes") or current.style_notes)
        rubric = compile_persona_rubric(merged_traits, new_stage, new_notes)
        now = datetime.now(UTC)

        synced_state = FriendPersonaState(
            user_id=resolved_user_id,
            version=max(current.version + 1, int(parsed.get("version") or (current.version + 1))),
            bond_stage=new_stage,
            affinity_score=new_affinity,
            current_mood=new_mood,
            traits=merged_traits,
            style_notes=new_notes,
            compiled_rubric=rubric,
            interaction_count=current.interaction_count,
            soul_sha256=disk_sha,
            last_reflected_at=current.last_reflected_at,
            updated_at=now,
        )
        with self._engine.begin() as conn:
            conn.execute(
                update(user_friend_personas)
                .where(user_friend_personas.c.user_id == resolved_user_id)
                .values(
                    version=synced_state.version,
                    bond_stage=synced_state.bond_stage,
                    affinity_score=synced_state.affinity_score,
                    current_mood=synced_state.current_mood,
                    traits_json=synced_state.traits,
                    style_notes_json=synced_state.style_notes,
                    compiled_rubric=synced_state.compiled_rubric,
                    soul_sha256=synced_state.soul_sha256,
                    updated_at=now,
                )
            )
        return synced_state

    def _default_user_id(self) -> str:
        with self._engine.connect() as conn:
            uid = conn.execute(
                select(user_friend_personas.c.user_id).order_by(user_friend_personas.c.updated_at.desc()).limit(1)
            ).scalar()
        return str(uid) if uid else "local"

    def _persist_and_mirror(self, state: FriendPersonaState) -> FriendPersonaState:
        soul_sha = self._write_soul_file(state)
        final_state = FriendPersonaState(
            user_id=state.user_id,
            version=state.version,
            bond_stage=state.bond_stage,
            affinity_score=state.affinity_score,
            current_mood=state.current_mood,
            traits=state.traits,
            style_notes=state.style_notes,
            compiled_rubric=state.compiled_rubric,
            interaction_count=state.interaction_count,
            soul_sha256=soul_sha,
            last_reflected_at=state.last_reflected_at,
            updated_at=state.updated_at,
        )
        with self._engine.begin() as conn:
            conn.execute(
                update(user_friend_personas)
                .where(user_friend_personas.c.user_id == state.user_id)
                .values(
                    version=final_state.version,
                    bond_stage=final_state.bond_stage,
                    affinity_score=final_state.affinity_score,
                    current_mood=final_state.current_mood,
                    traits_json=final_state.traits,
                    style_notes_json=final_state.style_notes,
                    compiled_rubric=final_state.compiled_rubric,
                    interaction_count=final_state.interaction_count,
                    soul_sha256=final_state.soul_sha256,
                    last_reflected_at=final_state.last_reflected_at,
                    updated_at=final_state.updated_at,
                )
            )
        return final_state

    def _write_soul_file(self, state: FriendPersonaState) -> str:
        markdown = render_soul_markdown(state)
        sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        path = self.soul_file_path(state.user_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(markdown, encoding="utf-8")
            try:
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return sha

    @staticmethod
    def _row_to_state(row: Mapping[str, Any]) -> FriendPersonaState:
        traits = normalize_traits(row.get("traits_json"))
        raw_notes = row.get("style_notes_json")
        style_notes = [str(item) for item in raw_notes] if isinstance(raw_notes, list) else []
        bond_stage = str(row.get("bond_stage") or "acquaintance")
        rubric = str(row.get("compiled_rubric") or "") or compile_persona_rubric(traits, bond_stage, style_notes)
        return FriendPersonaState(
            user_id=str(row["user_id"]),
            version=int(row.get("version") or 1),
            bond_stage=bond_stage,
            affinity_score=float(row.get("affinity_score") or 10.0),
            current_mood=str(row.get("current_mood") or "sereno"),
            traits=traits,
            style_notes=style_notes,
            compiled_rubric=rubric,
            interaction_count=int(row.get("interaction_count") or 0),
            soul_sha256=str(row.get("soul_sha256") or ""),
            last_reflected_at=row.get("last_reflected_at"),
            updated_at=row.get("updated_at"),
        )


def untainted_user_excerpt(history: Sequence[Mapping[str, Any]], *, max_chars: int = 16_000) -> str:
    """Extract only untainted human messages (`W3-R27` + Taint Gate).

    Never includes assistant output, tool results, MCP payloads, or tainted messages.
    """
    parts: list[str] = []
    remaining = max_chars
    for item in reversed(list(history)):
        if not isinstance(item, Mapping):
            continue
        role = item.get("role")
        content = item.get("content")
        if role != "user" or not isinstance(content, str) or not content.strip():
            continue
        if item.get("tainted") is True:
            continue
        if item.get("tool_call_id") is not None:
            continue
        if str(item.get("source") or "").lower() in {"tool", "mcp", "web", "system", "subagent"}:
            continue
        line = f"user: {content.strip()}"
        if len(line) > remaining:
            line = line[-remaining:]
        parts.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break
    return "\n\n".join(reversed(parts))[-max_chars:]


class FriendReflectionConsolidator:
    """Post-session consolidator for affective memories (`scope_type='friend'`) and damped persona evolution."""

    def __init__(
        self,
        provider: object | None = None,
        *,
        provider_name: str = "local",
        model_id: str = "default",
        persona_store: FriendPersonaStore,
        memory_store: PostgresAgentMemoryStore,
        user_id: str = "local",
    ) -> None:
        self.provider = provider
        self.provider_name = provider_name
        self.model_id = model_id
        self.persona_store = persona_store
        self.memory_store = memory_store
        self.user_id = user_id

    def run(self, history: Sequence[Mapping[str, Any]], *, user_id: str | None = None) -> dict[str, Any]:
        target_user = user_id or self.user_id
        excerpt = untainted_user_excerpt(history)
        if not excerpt or self.provider is None:
            return {"memories_saved": 0, "persona_updated": False}

        events = self.provider.stream({
            "turn_id": "friend-reflection",
            "provider": self.provider_name,
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": FRIEND_REFLECTION_INSTRUCTION},
                {"role": "user", "content": excerpt},
            ],
            "tools": [],
            "max_output_tokens": 900,
        })
        text_out = "".join(
            str(getattr(event, "text", "") or "")
            for event in events
            if str(getattr(event, "kind", "")) == "text"
        )
        return self.apply_reflection_text(text_out, user_id=target_user)

    def apply_reflection_text(self, raw_text: str, *, user_id: str | None = None) -> dict[str, Any]:
        target_user = user_id or self.user_id
        text_val = str(raw_text or "")
        start, end = text_val.find("{"), text_val.rfind("}")
        if start == -1 or end <= start:
            return {"memories_saved": 0, "persona_updated": False}
        try:
            payload = json.loads(text_val[start : end + 1])
        except ValueError:
            return {"memories_saved": 0, "persona_updated": False}
        if not isinstance(payload, Mapping):
            return {"memories_saved": 0, "persona_updated": False}

        memories_saved = 0
        raw_memories = payload.get("memories")
        if isinstance(raw_memories, Sequence) and not isinstance(raw_memories, (str, bytes)):
            for entry in raw_memories[:6]:
                if not isinstance(entry, Mapping):
                    continue
                fact = str(entry.get("fact") or "").strip()
                kind = str(entry.get("kind") or "episodic").strip().lower()
                if kind not in FRIEND_MEMORY_KINDS:
                    kind = "episodic"
                tags_raw = entry.get("tags")
                tags = (
                    tuple(str(t) for t in tags_raw if isinstance(t, str))
                    if isinstance(tags_raw, Sequence) and not isinstance(tags_raw, (str, bytes))
                    else ("friend-reflection",)
                )
                try:
                    valence = float(entry.get("emotional_valence", 0.0))
                except (TypeError, ValueError):
                    valence = 0.0
                try:
                    salience = float(entry.get("salience", 0.6))
                except (TypeError, ValueError):
                    salience = 0.6
                try:
                    self.memory_store.save(
                        fact,
                        tags,
                        kind=kind,
                        confidence=0.75,
                        source="consolidation",
                        scope_type="friend",
                        emotional_valence=valence,
                        salience=salience,
                    )
                    memories_saved += 1
                except Exception:  # noqa: BLE001 - skip invalid/injected memory without failing the rest
                    continue

        raw_deltas = payload.get("trait_deltas")
        trait_deltas = (
            {str(k): float(v) for k, v in raw_deltas.items() if k in DEFAULT_TRAITS and isinstance(v, (int, float))}
            if isinstance(raw_deltas, Mapping)
            else {}
        )
        raw_notes = payload.get("style_notes")
        safe_notes: list[str] = []
        if isinstance(raw_notes, Sequence) and not isinstance(raw_notes, (str, bytes)):
            for n in raw_notes:
                if isinstance(n, str):
                    try:
                        safe_notes.append(validate_style_note(n))
                    except ValueError:
                        continue
        mood = str(payload.get("mood") or "").strip() or None
        try:
            affinity_delta = float(payload.get("affinity_delta") or 0.0)
        except (TypeError, ValueError):
            affinity_delta = 0.0

        self.persona_store.apply_reflection_deltas(
            target_user,
            trait_deltas,
            mood=mood,
            new_style_notes=safe_notes,
            affinity_delta=affinity_delta,
        )
        return {"memories_saved": memories_saved, "persona_updated": True}


__all__ = [
    "DEFAULT_TRAITS",
    "FRIEND_REFLECTION_INSTRUCTION",
    "FriendPersonaState",
    "FriendPersonaStore",
    "FriendReflectionConsolidator",
    "MAX_TRAIT_DELTA_PER_SESSION",
    "TRAIT_BOUNDS",
    "VALID_BOND_STAGES",
    "bond_stage_for_affinity",
    "compile_persona_rubric",
    "normalize_traits",
    "parse_soul_markdown",
    "render_soul_markdown",
    "untainted_user_excerpt",
    "validate_style_note",
]
