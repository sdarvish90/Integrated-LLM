"""
Document Processor — Unified pipeline for all document types.
=============================================================

Processes:
- News articles, SEC filings, EPA permits, DOE awards (from raw text)
- PDF processor structured outputs (pre-extracted, import directly)
- PDF processor chunks (with optional LLM extraction)

All produce standardized ProcessedDocument output.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .domain_model import DomainModel
from .entity_extractor import EntityExtractor
from .pdf_structured_adapter import PDFStructuredAdapter
from .schema import (
    ChunkMetadata,
    DocumentType,
    Entity,
    EntityType,
    Fact,
    FactType,
    ProcessedDocument,
)

logger = logging.getLogger(__name__)

# ======================================================================
# Relevance threshold — documents below this are skipped
# ======================================================================
RELEVANCE_THRESHOLD = 0.55

# ======================================================================
# LLM extraction prompts
# ======================================================================

EXTRACTION_SYSTEM = (
    "You are an expert analyst extracting structured information from "
    "documents about blue hydrogen, ammonia, carbon capture, and energy "
    "infrastructure projects.\n\n"
    "Extract ALL relevant entities and facts. Be precise and conservative "
    "— only extract what is explicitly stated or clearly implied."
)

EXTRACTION_PROMPT = """\
Analyze this {document_type} document and extract structured information.

DOCUMENT:
{text}

Extract and return JSON:
{{
  "entities": [
    {{"type": "company|project|location|technology|product|contractor|agency|standard|equipment|facility",
      "value": "exact text",
      "normalized": "canonical form or null",
      "confidence": 0.0-1.0,
      "evidence": "supporting text span",
      "role": "optional role description",
      "sections_cited": ["optional list of sections for standards"]}}
  ],
  "facts": [
    {{"type": "status_change|capacity|investment|timeline|partnership|regulatory|risk|safety_requirement|numerical_threshold|compliance_obligation",
      "claim": "natural language statement",
      "confidence": 0.0-1.0,
      "evidence": "supporting text span",
      "related_entities": ["entity values"],
      "category": "optional category",
      "numerical_value": "optional numerical value with units",
      "standard_ref": "optional standard reference"}}
  ],
  "document_summary": "1-2 sentence summary of key content"
}}

Rules:
- Only extract what is explicitly stated
- Confidence reflects how certain the extraction is
- Include evidence spans that support each extraction
- Normalize company names (remove Inc., LLC, etc.)
- Normalize locations to "City, ST" or "City, Country"
- For capacity, include units in numerical_value
- For investment, include currency and whether confirmed or estimated
- For safety requirements, include the standard reference if mentioned
- For numerical thresholds, include the exact value and units"""


# ======================================================================
# DocumentProcessor
# ======================================================================


class DocumentProcessor:
    """Unified document processing pipeline.

    Usage::

        processor = DocumentProcessor()

        # Process raw text
        result = processor.process(text, document_type=DocumentType.SEC_FILING)

        # Import pre-extracted PDF processor output
        result = processor.import_structured("path/to/doc_structured.yaml")

        # Process PDF chunk with extraction
        result = processor.process_chunk("path/to/doc_001_section.md")

        if result:
            print(result.entities)
            print(result.facts)
    """

    # Source authority by document type
    SOURCE_AUTHORITY: Dict[DocumentType, float] = {
        DocumentType.SEC_FILING: 0.95,
        DocumentType.EPA_PERMIT: 0.92,
        DocumentType.DOE_AWARD: 0.92,
        DocumentType.REGULATION: 0.90,
        DocumentType.EPA_GUIDANCE: 0.88,
        DocumentType.INDUSTRY_STANDARD: 0.85,
        DocumentType.REGULATORY_FILING: 0.85,
        DocumentType.PDF_STRUCTURED: 0.80,
        DocumentType.PDF_CHUNK: 0.75,
        DocumentType.NEWS: 0.60,
        DocumentType.UNKNOWN: 0.50,
    }

    def __init__(
        self,
        domain_model: Optional[DomainModel] = None,
        entity_extractor: Optional[EntityExtractor] = None,
        llm_client: Optional[Any] = None,
        entity_resolver: Optional[Any] = None,
        relevance_threshold: float = RELEVANCE_THRESHOLD,
    ):
        """Initialize processor with optional dependency injection.

        Models are lazy-loaded on first use if not provided.
        """
        self._domain_model = domain_model
        self._entity_extractor = entity_extractor
        self._llm_client = llm_client
        self._entity_resolver = entity_resolver
        self._pdf_adapter = PDFStructuredAdapter()
        self._relevance_threshold = relevance_threshold
        self._initialized = False

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _ensure_initialized(self):
        """Lazy initialization of models."""
        if self._initialized:
            return

        if self._domain_model is None:
            self._domain_model = DomainModel()
            self._domain_model.load()

        if self._entity_extractor is None:
            self._entity_extractor = EntityExtractor()

        if self._llm_client is None:
            try:
                from llm_client import LLMClient
                self._llm_client = LLMClient()
            except ImportError:
                logger.warning("LLM client not available")

        if self._entity_resolver is None:
            try:
                from .entity_resolver import EntityResolver
                self._entity_resolver = EntityResolver()
            except Exception:
                logger.warning("EntityResolver not available")

        self._initialized = True

    # ==================================================================
    # Main processing methods
    # ==================================================================

    def process(
        self,
        text: str,
        document_type: DocumentType = DocumentType.UNKNOWN,
        document_id: Optional[str] = None,
        source_url: Optional[str] = None,
        skip_relevance_check: bool = False,
        pdf_metadata: Optional[Dict] = None,
    ) -> Optional[ProcessedDocument]:
        """Process a document from raw text.

        Args:
            text: Document text.
            document_type: Type of document (affects source_authority).
            document_id: Optional unique ID (generated if not provided).
            source_url: Optional source URL.
            skip_relevance_check: If True, skip domain relevance filter.
            pdf_metadata: Optional PDF metadata (page, section, filename).

        Returns:
            ProcessedDocument if relevant and successfully processed,
            None if irrelevant or processing failed.
        """
        start_time = time.time()
        self._ensure_initialized()

        if not document_id:
            document_id = self._generate_id(text, source_url)

        # Step 1: Relevance check (fast — embedding + cosine)
        if not skip_relevance_check:
            result = self._domain_model.classify(text)
            relevance_score = result['score']
            if relevance_score < self._relevance_threshold:
                logger.debug(
                    "Document %s below relevance threshold: %.2f",
                    document_id, relevance_score,
                )
                return None
        else:
            relevance_score = 0.75  # assumed relevant

        # Step 2: Extract entities and facts
        entities, facts, flags = self._extract(text, document_type)

        # Step 2b: Resolve entities against canonical registry
        if self._entity_resolver:
            try:
                entities = self._entity_resolver.resolve_entities(entities)
            except Exception as e:
                logger.warning("Entity resolution failed: %s", e)
                flags.append("resolution_error")

        # Step 3: Build result
        processing_time = int((time.time() - start_time) * 1000)

        chunk_metadata = None
        if pdf_metadata:
            chunk_metadata = ChunkMetadata(
                chunk_id=pdf_metadata.get('chunk_id', ''),
                section=pdf_metadata.get('section', ''),
                token_estimate=pdf_metadata.get('token_estimate', 0),
                key_entities=pdf_metadata.get('key_entities', []),
                key_concepts=pdf_metadata.get('key_concepts', []),
            )

        return ProcessedDocument(
            document_id=document_id,
            document_type=document_type,
            source_url=source_url,
            source_authority=self.SOURCE_AUTHORITY.get(document_type, 0.50),
            processed_at=datetime.now(),
            relevance_score=relevance_score,
            processing_time_ms=processing_time,
            entities=entities,
            facts=facts,
            text_snippet=text[:1000],
            full_text_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
            extraction_confidence=self._compute_confidence(entities, facts),
            flags=flags,
            chunk_metadata=chunk_metadata,
        )

    def import_structured(self, yaml_path: str) -> ProcessedDocument:
        """Import a pre-extracted ``*_structured.yaml`` from PDF processor.

        Bypasses extraction since the PDF processor already did it.
        """
        self._ensure_initialized()
        return self._pdf_adapter.load_structured_yaml(yaml_path)

    def process_chunk(
        self,
        chunk_path: str,
        extract_with_llm: bool = True,
    ) -> Optional[ProcessedDocument]:
        """Process a PDF chunk file (``*_NNN_section.md``).

        Returns None if the chunk is below the relevance threshold.
        """
        self._ensure_initialized()

        doc = self._pdf_adapter.load_chunk(chunk_path)

        # Relevance check
        if doc.text_snippet:
            result = self._domain_model.classify(doc.text_snippet)
            doc.relevance_score = result['score']

            if doc.relevance_score < self._relevance_threshold:
                logger.debug("Chunk %s below relevance threshold", doc.document_id)
                return None

        # Optionally extract entities/facts
        if extract_with_llm and doc.text_snippet:
            entities, facts, flags = self._extract(
                doc.text_snippet, DocumentType.PDF_CHUNK,
            )
            doc.entities = entities
            doc.facts = facts
            doc.extraction_confidence = self._compute_confidence(entities, facts)
            doc.flags = flags + ['pdf_chunk', 'llm_extracted']

        return doc

    def import_seeds(self, seeds_yaml_path: str) -> Dict[str, Any]:
        """Load seed examples from ``*_seeds.yaml`` for domain training.

        Returns dict with ``'positive'`` and ``'negative'`` example lists
        plus ``'source_document'`` metadata.
        """
        with open(seeds_yaml_path, 'r') as f:
            data = yaml.safe_load(f)

        positive = [ex.get('text', '') for ex in data.get('positive_examples', [])]
        negative = [ex.get('text', '') for ex in data.get('negative_examples', [])]

        return {
            'positive': positive,
            'negative': negative,
            'source_document': data.get('source_document', {}),
        }

    def process_batch(
        self,
        documents: List[Dict[str, Any]],
        parallel: bool = False,
    ) -> List[Optional[ProcessedDocument]]:
        """Process multiple documents.

        Args:
            documents: List of ``{"text": ..., "type": ..., "id": ..., "url": ...}``.
            parallel: Reserved for future parallel processing.

        Returns:
            List of ProcessedDocument (None entries for irrelevant/failed).
        """
        results: List[Optional[ProcessedDocument]] = []
        for doc_data in documents:
            doc_type = doc_data.get('type', DocumentType.UNKNOWN)
            if isinstance(doc_type, str):
                doc_type = DocumentType(doc_type)
            result = self.process(
                text=doc_data.get('text', ''),
                document_type=doc_type,
                document_id=doc_data.get('id'),
                source_url=doc_data.get('url'),
                pdf_metadata=doc_data.get('pdf_metadata'),
            )
            results.append(result)
        return results

    # ==================================================================
    # Extraction
    # ==================================================================

    def _extract(
        self,
        text: str,
        document_type: DocumentType,
    ) -> Tuple[List[Entity], List[Fact], List[str]]:
        """Extract entities and facts using LLM, with fallback.

        Returns ``(entities, facts, flags)``.
        """
        flags: List[str] = []

        # Truncate based on LLM context capacity.
        # Ollama llama3.1:8b has a 4096-token context; the prompt template
        # and system message consume ~600 tokens, leaving ~3400 for text +
        # output.  At ~4 chars/token the safe text budget is ~6000 chars.
        # Groq (70b) can handle 30K chars easily.
        if self._llm_client and getattr(self._llm_client, '_backend', None) == 'ollama':
            max_text = 6000
        else:
            max_text = 30000
        if len(text) > max_text:
            text = text[:max_text]
            flags.append('text_truncated')

        # Try LLM extraction first
        if self._llm_client:
            try:
                prompt = EXTRACTION_PROMPT.format(
                    document_type=document_type.value,
                    text=text,
                )
                response = self._llm_client.generate(
                    EXTRACTION_SYSTEM, prompt, max_tokens=2000,
                )
                if response:
                    entities, facts = self._parse_extraction_response(response)
                    return entities, facts, flags
                else:
                    flags.append('llm_empty_response')
            except Exception as e:
                logger.warning("LLM extraction failed: %s", e)
                flags.append(f'llm_error: {str(e)[:50]}')

        # Fallback to GLiNER entity extractor
        flags.append('fallback_extraction')
        return self._fallback_extract(text), [], flags

    def _parse_extraction_response(
        self, response: str,
    ) -> Tuple[List[Entity], List[Fact]]:
        """Parse LLM JSON response into Entity and Fact objects."""
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
        json_str = json_match.group(1) if json_match else response

        data = json.loads(json_str)

        entities: List[Entity] = []
        for e in data.get('entities', []):
            try:
                entity_type = EntityType(e.get('type', 'company'))
            except ValueError:
                entity_type = EntityType.COMPANY
            entities.append(Entity(
                type=entity_type,
                value=e.get('value', ''),
                normalized=e.get('normalized'),
                confidence=e.get('confidence', 0.5),
                evidence=e.get('evidence'),
                attributes=e.get('attributes', {}),
                role=e.get('role'),
                sections_cited=e.get('sections_cited', []),
            ))

        facts: List[Fact] = []
        for f in data.get('facts', []):
            try:
                fact_type = FactType(f.get('type', 'status_change'))
            except ValueError:
                fact_type = FactType.STATUS_CHANGE
            facts.append(Fact(
                type=fact_type,
                claim=f.get('claim', ''),
                confidence=f.get('confidence', 0.5),
                evidence=f.get('evidence'),
                entities=f.get('related_entities', []),
                attributes=f.get('attributes', {}),
                category=f.get('category'),
                hazard_addressed=f.get('hazard_addressed'),
                standard_ref=f.get('standard_ref'),
                numerical_value=f.get('numerical_value'),
            ))

        return entities, facts

    def _fallback_extract(self, text: str) -> List[Entity]:
        """GLiNER-based fallback extraction when LLM is unavailable."""
        if self._entity_extractor:
            try:
                return self._entity_extractor.extract(text)
            except Exception as e:
                logger.warning("Fallback extraction failed: %s", e)
        return []

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _generate_id(text: str, url: Optional[str]) -> str:
        """Generate unique document ID."""
        content = f"{url or ''}{text[:500]}"
        return hashlib.md5(content.encode()).hexdigest()[:16]

    @staticmethod
    def _compute_confidence(
        entities: List[Entity], facts: List[Fact],
    ) -> float:
        """Compute overall extraction confidence."""
        if not entities and not facts:
            return 0.0
        all_conf = [e.confidence for e in entities] + [f.confidence for f in facts]
        return sum(all_conf) / len(all_conf)
