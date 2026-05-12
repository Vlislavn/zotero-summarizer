from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.domain import ReadingPriority, normalize_reading_priority


class LLMConfig(BaseModel):
    draft_model: str = Field(..., min_length=1)
    refine_model: str = Field(..., min_length=1)
    api_base: str = Field(..., min_length=1)
    api_key_env: str = Field(..., min_length=1)


class PromptOverrides(BaseModel):
    map: Optional[str] = None
    reduce: Optional[str] = None
    refine: Optional[str] = None
    triage: Optional[str] = None


class CorpusConfig(BaseModel):
    enabled: bool = Field(default=True)
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2", min_length=1)
    similarity_threshold: float = Field(default=-0.30, ge=-1.0, le=1.0)
    stale_days_for_weak_negative: int = Field(default=30, ge=1, le=3650)


class GoalsConfig(BaseModel):
    research_goals: List[str] = Field(default_factory=list)
    triage_criteria: List[str] = Field(default_factory=list)
    relevance_scale: Dict[int, str]
    reading_priority_scale: Dict[str, str] = Field(default_factory=dict)
    summary_structure: List[str] = Field(default_factory=list)
    output_language: str = Field(default="English")
    llm: LLMConfig
    prompts: PromptOverrides = Field(default_factory=PromptOverrides)
    corpus: CorpusConfig = Field(default_factory=CorpusConfig)

    @field_validator("research_goals", "triage_criteria", "summary_structure")
    @classmethod
    def _non_empty_strings(cls, value: List[str]) -> List[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty item")
        return cleaned

    @field_validator("relevance_scale", mode="before")
    @classmethod
    def _normalize_relevance_scale_keys(cls, value: Any) -> Dict[int, str]:
        if not isinstance(value, dict):
            raise ValueError("relevance_scale must be a map of score to description")

        normalized: Dict[int, str] = {}
        for key, text in value.items():
            score = int(key)
            normalized[score] = str(text).strip()
        return normalized

    @field_validator("relevance_scale")
    @classmethod
    def _validate_relevance_scale(cls, value: Dict[int, str]) -> Dict[int, str]:
        expected = {1, 2, 3, 4, 5}
        if set(value.keys()) != expected:
            raise ValueError("relevance_scale must include keys 1,2,3,4,5")
        if any(not v for v in value.values()):
            raise ValueError("relevance_scale descriptions must be non-empty")
        return value


class SummarizeRequest(BaseModel):
    title: str = Field(..., min_length=1)
    doi: Optional[str] = None
    # pdf_path is required for the PDF pipeline (run_pipeline) but feed items
    # have no PDF when run_abstract_pipeline is called — empty string is the
    # documented sentinel for "abstract-only triage". The pipeline functions
    # validate presence themselves.
    pdf_path: str = ""
    abstract: Optional[str] = None


class TriageDimensions(BaseModel):
    goal_alignment: int = Field(default=3, ge=1, le=5)
    novelty_for_goals: int = Field(default=3, ge=1, le=5)
    methodological_rigor: int = Field(default=3, ge=1, le=5)
    actionability: int = Field(default=3, ge=1, le=5)
    evidence_strength: int = Field(default=3, ge=1, le=5)


class TriageResult(BaseModel):
    score: int = Field(..., ge=1, le=5)
    reading_priority: str = Field(default=ReadingPriority.COULD_READ.value)
    tags: List[str] = Field(default_factory=list)
    rationale: str = Field(..., min_length=1)
    dimensions: TriageDimensions | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("reading_priority")
    @classmethod
    def _validate_reading_priority(cls, value: str) -> str:
        return normalize_reading_priority(str(value or "").strip())


class RefinedSummary(BaseModel):
    executive_summary: str = Field(..., min_length=1)
    should_deep_read: str = Field(default="")
    key_sections_to_read: List[str] = Field(default_factory=list)
    relevance_to_research: str = Field(default="")
    controversial_points: str = Field(default="")
    industry_academy_impact: str = Field(default="")
    unknown_unknowns: str = Field(default="")
    implementation_quickstart: str = Field(default="")
    key_findings: List[str] = Field(default_factory=list)
    methods: str = Field(default="")
    limitations: str = Field(default="")

    @staticmethod
    def _coerce_text(value: Any, field_name: str, allow_bool: bool = False) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            if allow_bool:
                return "Yes" if value else "No"
            raise ValueError(f"{field_name} must be a string or list of strings")
        if isinstance(value, list):
            cleaned: List[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"{field_name} list items must be strings")
                stripped = item.strip()
                if stripped:
                    cleaned.append(stripped)
            return "; ".join(cleaned)
        if isinstance(value, str):
            return value.strip()
        raise ValueError(f"{field_name} must be a string or list of strings")

    @staticmethod
    def _coerce_text_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        text = str(value).strip()
        return [text] if text else []

    @field_validator("should_deep_read", mode="before")
    @classmethod
    def _coerce_should_deep_read(cls, value: Any) -> str:
        return cls._coerce_text(value, "should_deep_read", allow_bool=True)

    @field_validator("controversial_points", "unknown_unknowns", mode="before")
    @classmethod
    def _coerce_list_backed_string_fields(cls, value: Any) -> str:
        return cls._coerce_text(value, "field")

    @field_validator("key_sections_to_read", mode="before")
    @classmethod
    def _coerce_key_sections(cls, value: Any) -> List[str]:
        return cls._coerce_text_list(value)

    @field_validator("key_findings")
    @classmethod
    def _limit_key_findings(cls, value: List[str]) -> List[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        return cleaned[:10]

    @field_validator("key_findings", mode="before")
    @classmethod
    def _coerce_key_findings(cls, value: Any) -> List[str]:
        return cls._coerce_text_list(value)


class SummarizeResponse(BaseModel):
    executive_summary: str
    should_deep_read: str = ""
    key_sections_to_read: List[str] = Field(default_factory=list)
    relevance_to_research: str = ""
    controversial_points: str = ""
    industry_academy_impact: str = ""
    unknown_unknowns: str = ""
    implementation_quickstart: str = ""
    key_findings: List[str] = Field(default_factory=list)
    methods: str = ""
    limitations: str = ""
    relevance_score: int = Field(..., ge=1, le=5)
    composite_relevance_score: float = Field(default=0.0, ge=0.0, le=5.0)
    reading_priority: str = ReadingPriority.COULD_READ.value
    tags: List[str] = Field(default_factory=list)
    triage_rationale: str
    triage_dimensions: TriageDimensions | None = None
    triage_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    corpus_affinity_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    corpus_positive_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    corpus_negative_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_goal: str = ""
    matched_goal_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    suggested_collections: List[str] = Field(default_factory=list)
    top_similar_items: List[str] = Field(default_factory=list)


class BatchSummarizeItemRequest(BaseModel):
    item_id: str = Field(..., min_length=1)
    request: SummarizeRequest


class BatchSummarizeRequest(BaseModel):
    items: List[BatchSummarizeItemRequest] = Field(..., min_length=1, max_length=500)


class BatchSummarizeItemResponse(BaseModel):
    batch_id: str | None = None
    item_id: str
    title: str
    summary: SummarizeResponse
    normalized_score: float = Field(default=0.0, ge=0.0, le=100.0)
    percentile: float = Field(default=0.0, ge=0.0, le=100.0)
    rank: int = Field(default=0, ge=0)
    forced_priority: str = Field(default=ReadingPriority.COULD_READ.value)


class BatchFailure(BaseModel):
    item_id: str
    error: str


class CorpusItem(BaseModel):
    item_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    abstract: str = ""
    tags: List[str] = Field(default_factory=list)
    collections: List[str] = Field(default_factory=list)
    annotation_count: int = Field(default=0, ge=0)
    manual_note_count: int = Field(default=0, ge=0)
    created_at: str | None = None


class CorpusImportRequest(BaseModel):
    items: List[CorpusItem] = Field(default_factory=list, max_length=5000)


class CorpusImportResponse(BaseModel):
    imported_items: int = Field(default=0, ge=0)
    updated_items: int = Field(default=0, ge=0)


class FeedbackEvent(BaseModel):
    item_id: str = Field(..., min_length=1)
    feedback_type: str = Field(..., min_length=1)
    signal: str = Field(..., min_length=1)
    original_priority: str = ""
    inferred_relevance: float = Field(default=1.0, ge=1.0, le=5.0, description="Inferred relevance on the 1-5 triage scale")


class FeedbackRequest(BaseModel):
    events: List[FeedbackEvent] = Field(..., min_length=1, max_length=1000)


class TriageFeedbackRequest(BaseModel):
    verdict: Literal["approve", "reject"]


class TriageFeedbackResponse(BaseModel):
    item_id: str
    verdict: Literal["approve", "reject"]
    signal: str
    queued: int = Field(default=0, ge=0)


class TriageDimensionOverrideRequest(BaseModel):
    goal_alignment: int | None = Field(default=None, ge=1, le=5)
    novelty_for_goals: int | None = Field(default=None, ge=1, le=5)
    methodological_rigor: int | None = Field(default=None, ge=1, le=5)
    actionability: int | None = Field(default=None, ge=1, le=5)
    evidence_strength: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def _ensure_any_override(self) -> "TriageDimensionOverrideRequest":
        if all(
            value is None
            for value in [
                self.goal_alignment,
                self.novelty_for_goals,
                self.methodological_rigor,
                self.actionability,
                self.evidence_strength,
            ]
        ):
            raise ValueError("at least one triage dimension override is required")
        return self

    def to_partial_dimensions(self) -> Dict[str, int]:
        payload: Dict[str, int] = {}
        if self.goal_alignment is not None:
            payload["goal_alignment"] = int(self.goal_alignment)
        if self.novelty_for_goals is not None:
            payload["novelty_for_goals"] = int(self.novelty_for_goals)
        if self.methodological_rigor is not None:
            payload["methodological_rigor"] = int(self.methodological_rigor)
        if self.actionability is not None:
            payload["actionability"] = int(self.actionability)
        if self.evidence_strength is not None:
            payload["evidence_strength"] = int(self.evidence_strength)
        return payload


class CalibrationPeriodMetrics(BaseModel):
    total_feedback: int = Field(default=0, ge=0)
    approved_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    with_prediction_count: int = Field(default=0, ge=0)
    agreement_count: int = Field(default=0, ge=0)
    false_positive_count: int = Field(default=0, ge=0)
    false_negative_count: int = Field(default=0, ge=0)
    predicted_positive_count: int = Field(default=0, ge=0)
    actual_positive_count: int = Field(default=0, ge=0)
    true_positive_count: int = Field(default=0, ge=0)
    agreement_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)


class CalibrationMetricsResponse(BaseModel):
    periods: Dict[str, CalibrationPeriodMetrics] = Field(default_factory=dict)


class BatchSummarizeResponse(BaseModel):
    batch_id: str | None = None
    total_items: int = Field(..., ge=0)
    ranked_items: List[BatchSummarizeItemResponse] = Field(default_factory=list)
    failed_items: List[BatchFailure] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    config_loaded: bool
    draft_model: Optional[str] = None
    refine_model: Optional[str] = None
    api_base: Optional[str] = None


class AppState(BaseModel):
    config: GoalsConfig

    @model_validator(mode="after")
    def _validate_models(self) -> "AppState":
        if not self.config.llm.draft_model or not self.config.llm.refine_model:
            raise ValueError("both draft and refine models must be set")
        return self


class ZoteroStatusResponse(BaseModel):
    available: bool
    data_dir: str
    db_path: str
    stats: Dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ZoteroCollectionsResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class ZoteroItemsResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)


class TriageRunRequest(BaseModel):
    item_keys: List[str] = Field(..., min_length=1, max_length=500)
    queue_changes: bool = True

    @field_validator("item_keys")
    @classmethod
    def _normalize_item_keys(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        seen: set[str] = set()
        for item_key in value:
            key = str(item_key or "").strip()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        if not normalized:
            raise ValueError("item_keys must contain at least one non-empty item key")
        return normalized


class TriageRunResponse(BaseModel):
    job_id: str
    status: Literal["running", "completed", "failed"]
    total: int = Field(default=0, ge=0)


class PendingChangesResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class PendingChangeMutationRequest(BaseModel):
    change_ids: List[int] = Field(..., min_length=1, max_length=1000)
    force: bool = False

    @field_validator("change_ids")
    @classmethod
    def _normalize_change_ids(cls, value: List[int]) -> List[int]:
        normalized: List[int] = []
        seen: set[int] = set()
        for change_id in value:
            numeric = int(change_id)
            if numeric <= 0:
                continue
            if numeric in seen:
                continue
            seen.add(numeric)
            normalized.append(numeric)
        if not normalized:
            raise ValueError("change_ids must contain at least one positive integer")
        return normalized


class PendingPriorityOverrideRequest(BaseModel):
    item_key: str = Field(..., min_length=1, max_length=64)
    item_title: str = Field(default="")
    new_priority: str

    @field_validator("item_key")
    @classmethod
    def _normalize_item_key(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("item_key must be a non-empty string")
        return normalized

    @field_validator("item_title")
    @classmethod
    def _normalize_item_title(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("new_priority")
    @classmethod
    def _normalize_new_priority(cls, value: str) -> str:
        normalized = str(value or "").strip()
        coerced = normalize_reading_priority(normalized)
        if coerced != normalized:
            raise ValueError("new_priority must be one of must_read, should_read, could_read, dont_read")
        return coerced


class PendingChangeUpdateRequest(BaseModel):
    payload: Dict[str, Any]

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("payload must be an object")
        return value


class ZoteroItemPriorityUpdateRequest(BaseModel):
    priority: str
    force: bool = False

    @field_validator("priority")
    @classmethod
    def _normalize_priority(cls, value: str) -> str:
        normalized = str(value or "").strip()
        coerced = normalize_reading_priority(normalized)
        if coerced != normalized:
            raise ValueError("priority must be one of must_read, should_read, could_read, dont_read")
        return coerced


class ZoteroItemTagUpdateRequest(BaseModel):
    add_tags: List[str] = Field(default_factory=list)
    remove_tags: List[str] = Field(default_factory=list)
    force: bool = False

    @staticmethod
    def _normalize_tags(value: List[str]) -> List[str]:
        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = str(raw or "").strip()
            if not tag:
                continue
            folded = tag.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            cleaned.append(tag)
        return cleaned

    @field_validator("add_tags", "remove_tags", mode="before")
    @classmethod
    def _coerce_tag_lists(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            return [part.strip() for part in text.split(",") if part.strip()]
        raise ValueError("tag fields must be a list of strings")

    @field_validator("add_tags", "remove_tags")
    @classmethod
    def _normalize_tag_lists(cls, value: List[str]) -> List[str]:
        return cls._normalize_tags(value)

    @model_validator(mode="after")
    def _ensure_non_empty_update(self) -> "ZoteroItemTagUpdateRequest":
        if not self.add_tags and not self.remove_tags:
            raise ValueError("at least one tag must be added or removed")
        return self


class ZoteroCollectionRef(BaseModel):
    collection_key: str = ""
    collection_path: str = ""

    @field_validator("collection_key", "collection_path")
    @classmethod
    def _normalize_collection_fields(cls, value: str) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _ensure_identifier(self) -> "ZoteroCollectionRef":
        if not self.collection_key and not self.collection_path:
            raise ValueError("collection_key or collection_path is required")
        return self

    def to_writer_payload(self) -> Dict[str, str]:
        payload: Dict[str, str] = {}
        if self.collection_key:
            payload["collection_key"] = self.collection_key
        if self.collection_path:
            payload["collection_path"] = self.collection_path
        return payload


class ZoteroItemCollectionUpdateRequest(BaseModel):
    add: List[ZoteroCollectionRef] = Field(default_factory=list)
    remove: List[ZoteroCollectionRef] = Field(default_factory=list)
    force: bool = False

    @model_validator(mode="after")
    def _ensure_any_collection_change(self) -> "ZoteroItemCollectionUpdateRequest":
        if not self.add and not self.remove:
            raise ValueError("at least one collection must be added or removed")
        return self
