"""
Schema — Standardized data structures for extracted information.
================================================================

All document types produce the same output format, enabling
unified downstream processing. Supports both:
1. Fresh extraction from raw text
2. Import of pre-extracted data from PDF processor
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class DocumentType(Enum):
    """Types of documents the system can process."""
    NEWS = "news"
    SEC_FILING = "sec_filing"
    EPA_PERMIT = "epa_permit"
    DOE_AWARD = "doe_award"
    REGULATORY_FILING = "regulatory_filing"

    # PDF Processor outputs
    PDF_STRUCTURED = "pdf_structured"
    PDF_CHUNK = "pdf_chunk"
    EPA_GUIDANCE = "epa_guidance"
    INDUSTRY_STANDARD = "industry_standard"
    REGULATION = "regulation"

    UNKNOWN = "unknown"


@dataclass
class LocationMatch:
    """Result of location normalization."""
    state: Optional[str] = None       # "LA"
    city: Optional[str] = None        # "Donaldsonville"
    region: Optional[str] = None      # "gulf_coast"
    confidence: float = 0.0


class EntityType(Enum):
    """Types of entities that can be extracted."""
    COMPANY = "company"
    PROJECT = "project"
    LOCATION = "location"
    PERSON = "person"
    TECHNOLOGY = "technology"
    PRODUCT = "product"
    AGENCY = "agency"
    CONTRACTOR = "contractor"
    FINANCIAL = "financial"
    FACILITY = "facility"

    # From PDF processor / regulatory documents
    STANDARD = "standard"
    REGULATION = "regulation"
    EQUIPMENT = "equipment"


class FactType(Enum):
    """Types of facts/claims that can be extracted."""
    STATUS_CHANGE = "status_change"
    CAPACITY = "capacity"
    INVESTMENT = "investment"
    TIMELINE = "timeline"
    PARTNERSHIP = "partnership"
    REGULATORY = "regulatory"
    RISK = "risk"
    TECHNOLOGY_DETAIL = "technology"
    MARKET_DATA = "market_data"

    # From PDF processor / regulatory documents
    SAFETY_REQUIREMENT = "safety_requirement"
    NUMERICAL_THRESHOLD = "numerical_threshold"
    COMPLIANCE_OBLIGATION = "compliance_obligation"


# ======================================================================
# Core data classes
# ======================================================================


@dataclass
class Entity:
    """An extracted entity (company, project, location, standard, etc.)."""
    type: EntityType
    value: str
    normalized: Optional[str] = None
    confidence: float = 0.0
    evidence: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    role: Optional[str] = None
    sections_cited: List[str] = field(default_factory=list)

    # Tier 4: Entity Resolution
    canonical_key: Optional[str] = None       # "air_products"
    canonical_name: Optional[str] = None      # "Air Products"
    resolution_method: Optional[str] = None   # "exact_canonical", "alias", "fuzzy", "cik", "epa_id", "unresolved"
    resolution_confidence: float = 0.0

    def to_dict(self) -> Dict:
        return {
            'type': self.type.value,
            'value': self.value,
            'normalized': self.normalized,
            'confidence': self.confidence,
            'evidence': self.evidence,
            'attributes': self.attributes,
            'role': self.role,
            'sections_cited': self.sections_cited,
            'canonical_key': self.canonical_key,
            'canonical_name': self.canonical_name,
            'resolution_method': self.resolution_method,
            'resolution_confidence': self.resolution_confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> Entity:
        return cls(
            type=EntityType(data['type']),
            value=data['value'],
            normalized=data.get('normalized'),
            confidence=data.get('confidence', 0.0),
            evidence=data.get('evidence'),
            attributes=data.get('attributes', {}),
            role=data.get('role'),
            sections_cited=data.get('sections_cited', []),
            canonical_key=data.get('canonical_key'),
            canonical_name=data.get('canonical_name'),
            resolution_method=data.get('resolution_method'),
            resolution_confidence=data.get('resolution_confidence', 0.0),
        )


@dataclass
class Fact:
    """An extracted fact or claim about a project/company/requirement."""
    type: FactType
    claim: str
    confidence: float = 0.0
    evidence: Optional[str] = None
    entities: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    category: Optional[str] = None
    hazard_addressed: Optional[str] = None
    standard_ref: Optional[str] = None
    numerical_value: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            'type': self.type.value,
            'claim': self.claim,
            'confidence': self.confidence,
            'evidence': self.evidence,
            'entities': self.entities,
            'attributes': self.attributes,
            'category': self.category,
            'hazard_addressed': self.hazard_addressed,
            'standard_ref': self.standard_ref,
            'numerical_value': self.numerical_value,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> Fact:
        return cls(
            type=FactType(data['type']),
            claim=data['claim'],
            confidence=data.get('confidence', 0.0),
            evidence=data.get('evidence'),
            entities=data.get('entities', []),
            attributes=data.get('attributes', {}),
            category=data.get('category'),
            hazard_addressed=data.get('hazard_addressed'),
            standard_ref=data.get('standard_ref'),
            numerical_value=data.get('numerical_value'),
        )


# ======================================================================
# PDF processor metadata
# ======================================================================


@dataclass
class FigureMetadata:
    """Metadata for a figure extracted from a PDF."""
    figure_id: str = ""
    page: int = 0
    caption: str = ""
    figure_type: str = ""       # diagram, chart, schematic, photo, flowchart
    description: str = ""       # Vision LLM description
    extracted_data: Dict[str, Any] = field(default_factory=dict)
    bbox: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'figure_id': self.figure_id,
            'page': self.page,
            'caption': self.caption,
            'figure_type': self.figure_type,
            'description': self.description,
            'extracted_data': self.extracted_data,
            'bbox': self.bbox,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> FigureMetadata:
        return cls(
            figure_id=data.get('figure_id', ''),
            page=data.get('page', 0),
            caption=data.get('caption', ''),
            figure_type=data.get('figure_type', ''),
            description=data.get('description', ''),
            extracted_data=data.get('extracted_data', {}),
            bbox=data.get('bbox', []),
        )


@dataclass
class TableData:
    """Structured table extracted from a PDF."""
    table_id: str = ""
    page: int = 0
    caption: str = ""
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    bbox: List[float] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render as markdown table for inclusion in chunks."""
        if not self.headers:
            return ""
        lines = []
        lines.append("| " + " | ".join(self.headers) + " |")
        lines.append("|" + "|".join("---" for _ in self.headers) + "|")
        for row in self.rows:
            padded = row + [""] * (len(self.headers) - len(row))
            lines.append("| " + " | ".join(padded[:len(self.headers)]) + " |")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {
            'table_id': self.table_id,
            'page': self.page,
            'caption': self.caption,
            'headers': self.headers,
            'rows': self.rows,
            'bbox': self.bbox,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> TableData:
        return cls(
            table_id=data.get('table_id', ''),
            page=data.get('page', 0),
            caption=data.get('caption', ''),
            headers=data.get('headers', []),
            rows=data.get('rows', []),
            bbox=data.get('bbox', []),
        )


@dataclass
class ChunkMetadata:
    """Metadata for PDF chunks (from \\*_001_\\*.md files)."""
    chunk_id: str = ""
    section: str = ""
    token_estimate: int = 0
    key_entities: List[str] = field(default_factory=list)
    key_concepts: List[str] = field(default_factory=list)
    # Section structure
    section_numbers: List[str] = field(default_factory=list)
    section_title: str = ""
    # Chunk linking
    prev_chunk: str = ""
    next_chunk: str = ""
    cross_references: List[str] = field(default_factory=list)
    context_overlap: str = ""
    # Attached media
    figures: List[FigureMetadata] = field(default_factory=list)
    tables: List[TableData] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'chunk_id': self.chunk_id,
            'section': self.section,
            'token_estimate': self.token_estimate,
            'key_entities': self.key_entities,
            'key_concepts': self.key_concepts,
            'section_numbers': self.section_numbers,
            'section_title': self.section_title,
            'prev_chunk': self.prev_chunk,
            'next_chunk': self.next_chunk,
            'cross_references': self.cross_references,
            'context_overlap': self.context_overlap,
            'figures': [f.to_dict() for f in self.figures],
            'tables': [t.to_dict() for t in self.tables],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> ChunkMetadata:
        return cls(
            chunk_id=data.get('chunk_id', ''),
            section=data.get('section', ''),
            token_estimate=data.get('token_estimate', 0),
            key_entities=data.get('key_entities', []),
            key_concepts=data.get('key_concepts', []),
            section_numbers=data.get('section_numbers', []),
            section_title=data.get('section_title', ''),
            prev_chunk=data.get('prev_chunk', ''),
            next_chunk=data.get('next_chunk', ''),
            cross_references=data.get('cross_references', []),
            context_overlap=data.get('context_overlap', ''),
            figures=[FigureMetadata.from_dict(f) for f in data.get('figures', [])],
            tables=[TableData.from_dict(t) for t in data.get('tables', [])],
        )


@dataclass
class StructuredDocMetadata:
    """Metadata from \\*_structured.yaml ``system_identity`` section."""
    doc_id: str = ""
    doc_type: str = ""
    publisher: str = ""
    office: Optional[str] = None
    source_date: Optional[str] = None
    priority: str = "low"
    domain: str = ""
    purpose: Optional[str] = None
    referenced_standard: Optional[str] = None
    regulatory_context: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            'doc_id': self.doc_id,
            'doc_type': self.doc_type,
            'publisher': self.publisher,
            'office': self.office,
            'source_date': self.source_date,
            'priority': self.priority,
            'domain': self.domain,
            'purpose': self.purpose,
            'referenced_standard': self.referenced_standard,
            'regulatory_context': self.regulatory_context,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> StructuredDocMetadata:
        return cls(
            doc_id=data.get('doc_id', ''),
            doc_type=data.get('doc_type', ''),
            publisher=data.get('publisher', ''),
            office=data.get('office'),
            source_date=data.get('source_date'),
            priority=data.get('priority', 'low'),
            domain=data.get('domain', ''),
            purpose=data.get('purpose'),
            referenced_standard=data.get('referenced_standard'),
            regulatory_context=data.get('regulatory_context'),
        )


@dataclass
class RegulatoryReference:
    """A reference to a regulation or standard."""
    id: str
    title: str
    description: Optional[str] = None
    sections: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'sections': self.sections,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> RegulatoryReference:
        return cls(
            id=data.get('id', ''),
            title=data.get('title', ''),
            description=data.get('description'),
            sections=data.get('sections', []),
        )


# ======================================================================
# Top-level document
# ======================================================================


@dataclass
class ProcessedDocument:
    """Standardized output from document processing.

    Same structure regardless of whether input was a news article,
    SEC filing, EPA permit, PDF processor structured output, or chunk.
    """
    # Source information
    document_id: str
    document_type: DocumentType
    source_url: Optional[str] = None
    source_authority: float = 0.5

    # Processing metadata
    processed_at: datetime = field(default_factory=datetime.now)
    relevance_score: float = 0.0
    processing_time_ms: int = 0

    # Extracted content
    entities: List[Entity] = field(default_factory=list)
    facts: List[Fact] = field(default_factory=list)

    # Raw text (truncated for storage)
    text_snippet: str = ""
    full_text_hash: str = ""

    # Quality indicators
    extraction_confidence: float = 0.0
    flags: List[str] = field(default_factory=list)

    # PDF processor metadata (optional)
    chunk_metadata: Optional[ChunkMetadata] = None
    structured_metadata: Optional[StructuredDocMetadata] = None

    # Regulatory references (from structured output)
    regulatory_references: List[RegulatoryReference] = field(default_factory=list)

    def to_dict(self) -> Dict:
        result: Dict[str, Any] = {
            'document_id': self.document_id,
            'document_type': self.document_type.value,
            'source_url': self.source_url,
            'source_authority': self.source_authority,
            'processed_at': self.processed_at.isoformat(),
            'relevance_score': self.relevance_score,
            'processing_time_ms': self.processing_time_ms,
            'entities': [e.to_dict() for e in self.entities],
            'facts': [f.to_dict() for f in self.facts],
            'text_snippet': self.text_snippet,
            'full_text_hash': self.full_text_hash,
            'extraction_confidence': self.extraction_confidence,
            'flags': self.flags,
            'regulatory_references': [r.to_dict() for r in self.regulatory_references],
        }
        if self.chunk_metadata:
            result['chunk_metadata'] = self.chunk_metadata.to_dict()
        if self.structured_metadata:
            result['structured_metadata'] = self.structured_metadata.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict) -> ProcessedDocument:
        chunk_meta = None
        if data.get('chunk_metadata'):
            chunk_meta = ChunkMetadata.from_dict(data['chunk_metadata'])

        struct_meta = None
        if data.get('structured_metadata'):
            struct_meta = StructuredDocMetadata.from_dict(data['structured_metadata'])

        return cls(
            document_id=data['document_id'],
            document_type=DocumentType(data['document_type']),
            source_url=data.get('source_url'),
            source_authority=data.get('source_authority', 0.5),
            processed_at=(
                datetime.fromisoformat(data['processed_at'])
                if data.get('processed_at') else datetime.now()
            ),
            relevance_score=data.get('relevance_score', 0.0),
            processing_time_ms=data.get('processing_time_ms', 0),
            entities=[Entity.from_dict(e) for e in data.get('entities', [])],
            facts=[Fact.from_dict(f) for f in data.get('facts', [])],
            text_snippet=data.get('text_snippet', ''),
            full_text_hash=data.get('full_text_hash', ''),
            extraction_confidence=data.get('extraction_confidence', 0.0),
            flags=data.get('flags', []),
            chunk_metadata=chunk_meta,
            structured_metadata=struct_meta,
            regulatory_references=[
                RegulatoryReference.from_dict(r)
                for r in data.get('regulatory_references', [])
            ],
        )

    @staticmethod
    def compute_text_hash(text: str) -> str:
        """Compute hash of text for deduplication."""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


# ======================================================================
# Raw document (pre-processing input)
# ======================================================================


@dataclass
class RawDocument:
    """Unprocessed document fetched from a source adapter.

    Must be fed through :class:`DocumentProcessor` to produce a
    :class:`ProcessedDocument`.  Every source adapter converts its
    native output into this standard envelope.
    """
    document_id: str
    document_type: DocumentType
    source_name: str                                    # "sec_edgar", "epa_echo", "news_rss"
    raw_text: str
    text_snippet: str = ""
    source_url: Optional[str] = None
    published_date: Optional[str] = None
    title: Optional[str] = None
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=datetime.now)
    fetch_method: str = ""

    def to_processor_kwargs(self) -> Dict[str, Any]:
        """Convert to kwargs for ``DocumentProcessor.process()``."""
        return {
            'text': self.raw_text,
            'document_type': self.document_type,
            'document_id': self.document_id,
            'source_url': self.source_url,
        }
