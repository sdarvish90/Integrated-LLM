"""NLP pipeline modules for the 6-tier semantic intelligence pipeline."""

from .domain_model import DomainModel, get_domain_model
from .schema import (
    DocumentType, EntityType, FactType,
    Entity, Fact, ProcessedDocument, RawDocument,
    ChunkMetadata, StructuredDocMetadata, RegulatoryReference,
    FigureMetadata, TableData,
)
from .pdf_structured_adapter import PDFStructuredAdapter
from .document_processor import DocumentProcessor
from .entity_extractor import EntityExtractor, ArticleExtraction, get_extractor

# Tier 4: Entity & Concept Resolution
from .entity_registry import EntityRegistry
from .concept_registry import ConceptRegistry
from .entity_resolver import EntityResolver, CanonicalEntity

# Tier 5: Evidence Aggregator
from .evidence_aggregator import EvidenceAggregator, STAGE_ORDER
from .project_store import (
    ProjectStore, Project, ProjectStage, EvidenceItem,
    StageAssessment, ValueChain, EndUseSector, SOURCE_AUTHORITY,
)
