"""Data shapes for Targeted Search — the query-driven pull surface.

A ``ResearchSession`` is the persisted unit: a raw topic parsed into a
``SearchIntent`` + per-source ``QueryPlan``, the federated ``Candidate`` list
(each accreting its query score, quality, and targeted review as it flows the
pipeline), and a status. All are plain dataclasses with explicit ``to_dict`` /
``from_dict`` so the JSON session store round-trips exactly (spec §10).
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from zotero_summarizer.domain import normalize_arxiv_id, normalize_doi

# Version-family taxonomy (spec §11). A preprint and its journal article are ONE
# family with a preferred version — never destructively merged.
VERSION_TYPES = (
    "preprint",
    "version_of_record",
    "correction",
    "retraction",
    "unknown",
)


@dataclass(slots=True)
class Provenance:
    """Why a candidate is here: which source/query channel surfaced it, and
    where it ranked in THAT source (citation-weighted for OpenAlex → a
    candidate-generation signal only, never our relevance signal)."""

    source: str
    query_variant: str
    source_rank: int = 0
    retrieved_at: str = ""


@dataclass(slots=True)
class Candidate:
    """One federated paper (a version family after dedup). Identifiers are
    normalized at construction so dedup keys compare equal. Scores/review are
    filled in downstream; ``{}``/``None`` until then."""

    title: str
    abstract: str = ""
    doi: str = ""
    arxiv_id: str = ""
    pmid: str = ""
    pmcid: str = ""
    openalex_id: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    url: str = ""
    is_open_access: bool = False
    is_retracted: bool = False
    version_type: str = "unknown"
    version_family_id: str = ""
    provenance: list[Provenance] = field(default_factory=list)
    # Derived downstream:
    query_score: float | None = None   # master key — OUR cross-encoder vs the query
    goal_sim: float | None = None      # weak tiebreak (standing goals); often absent
    quality: dict[str, Any] = field(default_factory=dict)   # query-independent QualityEval
    coverage: dict[str, Any] = field(default_factory=dict)  # sections used/missing (§9)
    verdict: str = ""                  # reading intent (reversible)
    review: dict[str, Any] = field(default_factory=dict)    # brief + query_lens + Q answers
    materialized_zotero_key: str = ""  # set only on explicit Inbox push

    def __post_init__(self) -> None:
        self.doi = normalize_doi(self.doi) if self.doi else ""
        self.arxiv_id = normalize_arxiv_id(self.arxiv_id) if self.arxiv_id else ""
        self.pmid = (self.pmid or "").strip()
        self.pmcid = (self.pmcid or "").strip()

    @property
    def candidate_id(self) -> str:
        """Stable identity for dedup / session keys: first available normalized
        identifier, else a title hash (never title-alone for MERGING — see
        ``dedup.py`` — only as a last-resort self-id)."""
        for ident in (self.doi, self.arxiv_id, self.pmid, self.pmcid, self.openalex_id):
            if ident:
                return ident
        return "title:" + hashlib.sha1(self.title.lower().encode("utf-8")).hexdigest()[:16]

    def identifier_keys(self) -> list[str]:
        """Namespaced identifier tokens for the dedup graph (empty ones dropped)."""
        pairs = (
            ("doi", self.doi), ("arxiv", self.arxiv_id), ("pmid", self.pmid),
            ("pmcid", self.pmcid), ("openalex", self.openalex_id),
        )
        return [f"{ns}:{val}" for ns, val in pairs if val]

    def to_scoring_dict(self) -> dict[str, Any]:
        """The canonical dict the gate/reranker consume (reading_queue.live_scoring)."""
        return {
            "item_key": self.candidate_id,
            "title": self.title,
            "abstract": self.abstract,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "authors": ", ".join(self.authors),
            "year": self.year,
            "publication_title": self.venue,
            "url": self.url,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        data = dict(data)
        prov = [Provenance(**p) for p in data.pop("provenance", []) or []]
        known = {f for f in cls.__dataclass_fields__ if f != "provenance"}
        cand = cls(**{k: v for k, v in data.items() if k in known})
        cand.provenance = prov
        return cand


@dataclass(slots=True)
class SearchIntent:
    """Structured parse of the raw topic (spec §13.1). ``canonical_question`` is
    the English paragraph used for the semantic + library-expanded channels."""

    raw_query: str
    canonical_question: str = ""
    concepts: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    must_not_include: list[str] = field(default_factory=list)
    study_types: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    # False when the LLM parse failed and we fell back to the raw query — so a
    # degraded plan is visible in the UI, never silently passed off as planned.
    parse_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchIntent":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(slots=True)
class QueryPlan:
    """Per-source query strings (spec §13.1). Shown to the user AS the plan —
    not a single reformulated query."""

    library_raw: str = ""
    library_expanded: str = ""
    openalex_lexical: str = ""
    openalex_semantic: str = ""
    europepmc: str = ""
    arxiv: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryPlan":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def display(self) -> list[dict[str, str]]:
        """Flat [{source, query}] rows for the transparency panel."""
        rows = [
            ("library", self.library_expanded or self.library_raw),
            ("openalex (lexical)", self.openalex_lexical),
            ("openalex (semantic)", self.openalex_semantic),
            ("europepmc", self.europepmc),
            ("arxiv", self.arxiv),
        ]
        return [{"source": s, "query": q} for s, q in rows if q]


@dataclass(slots=True)
class ResearchSession:
    """The persisted unit (spec §10)."""

    id: str
    created_at: str
    raw_query: str
    intent: SearchIntent
    plan: QueryPlan
    questions: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    status: str = "created"
    screened_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "raw_query": self.raw_query,
            "intent": self.intent.to_dict(),
            "plan": self.plan.to_dict(),
            "questions": list(self.questions),
            "candidates": [c.to_dict() for c in self.candidates],
            "status": self.status,
            "screened_count": self.screened_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchSession":
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            raw_query=data["raw_query"],
            intent=SearchIntent.from_dict(data.get("intent") or {"raw_query": data["raw_query"]}),
            plan=QueryPlan.from_dict(data.get("plan") or {}),
            questions=list(data.get("questions") or []),
            candidates=[Candidate.from_dict(c) for c in data.get("candidates") or []],
            status=data.get("status", "created"),
            screened_count=int(data.get("screened_count") or 0),
        )
