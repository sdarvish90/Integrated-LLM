"""
PDF Structured Adapter — Import pre-extracted data from PDF processor.
=====================================================================

The PDF processor outputs ``*_structured.yaml`` files containing
already-extracted entities, facts, and metadata.  This adapter converts
them to :class:`ProcessedDocument` objects, avoiding redundant
re-extraction.

It also handles ``*_NNN_section.md`` chunk files produced by the same
processor.

Usage::

    adapter = PDFStructuredAdapter()
    doc = adapter.load_structured_yaml("path/to/doc_structured.yaml")
    chunks = adapter.load_chunks("path/to/doc_001_section.md",
                                 "path/to/doc_002_section.md")
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .schema import (
    ChunkMetadata,
    DocumentType,
    Entity,
    EntityType,
    Fact,
    FactType,
    FigureMetadata,
    ProcessedDocument,
    RegulatoryReference,
    StructuredDocMetadata,
    TableData,
)

logger = logging.getLogger(__name__)


class PDFStructuredAdapter:
    """Convert PDF processor outputs to :class:`ProcessedDocument` objects.

    Handles two types of output:

    1. ``*_structured.yaml`` — full structured extraction (import directly)
    2. ``*_NNN_section.md`` — chunked sections (parse and process)
    """

    # Document type mapping
    DOC_TYPE_MAP: Dict[str, DocumentType] = {
        'regulation': DocumentType.REGULATION,
        'guidance': DocumentType.EPA_GUIDANCE,
        'standard': DocumentType.INDUSTRY_STANDARD,
        'permit': DocumentType.EPA_PERMIT,
        'report': DocumentType.PDF_STRUCTURED,
    }

    # Source authority by publisher keyword
    AUTHORITY_MAP: Dict[str, float] = {
        'epa': 0.92,
        'environmental protection agency': 0.92,
        'doe': 0.90,
        'department of energy': 0.90,
        'ferc': 0.90,
        'sec': 0.95,
        'ansi': 0.88,
        'cga': 0.85,
    }
    _DEFAULT_AUTHORITY = 0.75

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_structured_yaml(self, yaml_path: str) -> ProcessedDocument:
        """Load a ``*_structured.yaml`` file and convert."""
        path = Path(yaml_path)
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return self._convert_structured(data, path)

    def load_chunk(self, chunk_path: str) -> ProcessedDocument:
        """Load a single chunk markdown file (``*_NNN_section.md``)."""
        path = Path(chunk_path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        return self._parse_chunk(content, path)

    def load_chunks(self, *chunk_paths: str) -> List[ProcessedDocument]:
        """Load multiple chunk files."""
        return [self.load_chunk(p) for p in chunk_paths]

    # ------------------------------------------------------------------
    # Structured YAML conversion
    # ------------------------------------------------------------------

    def _convert_structured(self, data: Dict, source_path: Path) -> ProcessedDocument:
        identity = data.get('system_identity', data)

        metadata = StructuredDocMetadata(
            doc_id=identity.get('doc_id', source_path.stem),
            doc_type=identity.get('doc_type', ''),
            publisher=identity.get('publisher', ''),
            office=identity.get('office'),
            source_date=identity.get('source_date'),
            priority=identity.get('priority', 'low'),
            domain=identity.get('domain', ''),
            purpose=identity.get('purpose'),
            referenced_standard=identity.get('referenced_standard'),
            regulatory_context=identity.get('regulatory_context'),
        )

        entities = self._extract_entities(data)
        facts = self._extract_facts(data)
        reg_refs = self._extract_regulatory_refs(data)
        doc_type = self._map_doc_type(identity.get('doc_type', ''))
        authority = self._get_authority(identity.get('publisher', ''))

        return ProcessedDocument(
            document_id=metadata.doc_id,
            document_type=doc_type,
            source_authority=authority,
            relevance_score=0.85,  # pre-curated = likely relevant
            processed_at=datetime.now(),
            entities=entities,
            facts=facts,
            structured_metadata=metadata,
            regulatory_references=reg_refs,
            extraction_confidence=0.95,
            flags=['pre_extracted', 'pdf_processor', 'structured_yaml'],
        )

    # ------------------------------------------------------------------
    # Entity extraction from structured data
    # ------------------------------------------------------------------

    def _extract_entities(self, data: Dict) -> List[Entity]:
        entities: List[Entity] = []
        key_entities = data.get('key_entities', {})

        for org in key_entities.get('organizations', []):
            attrs = {}
            if org.get('office'):
                attrs['office'] = org['office']
            entities.append(Entity(
                type=EntityType.AGENCY,
                value=org.get('name', ''),
                role=org.get('role'),
                confidence=0.95,
                attributes=attrs,
            ))

        for std in key_entities.get('standards', []):
            attrs = {}
            if std.get('usage'):
                attrs['usage'] = std['usage']
            entities.append(Entity(
                type=EntityType.STANDARD,
                value=std.get('id', ''),
                normalized=std.get('title'),
                sections_cited=std.get('sections_cited', []),
                confidence=0.95,
                attributes=attrs,
            ))

        for equip in key_entities.get('equipment', []):
            entities.append(Entity(
                type=EntityType.EQUIPMENT,
                value=equip.get('name', ''),
                confidence=0.90,
                attributes=equip.get('attributes', {}),
            ))

        for fac in key_entities.get('facilities', []):
            entities.append(Entity(
                type=EntityType.FACILITY,
                value=fac.get('name', ''),
                confidence=0.90,
                attributes=fac.get('attributes', {}),
            ))

        return entities

    # ------------------------------------------------------------------
    # Fact extraction from structured data
    # ------------------------------------------------------------------

    def _extract_facts(self, data: Dict) -> List[Fact]:
        facts: List[Fact] = []

        # Safety requirements
        safety_reqs = data.get('safety_requirements', {})
        for _section_key, section_data in safety_reqs.items():
            if not isinstance(section_data, dict):
                continue
            hazard = section_data.get('hazard_addressed', '')
            for req in section_data.get('requirements', []):
                if not isinstance(req, dict):
                    continue
                facts.append(Fact(
                    type=FactType.SAFETY_REQUIREMENT,
                    claim=req.get('requirement', ''),
                    category=req.get('category'),
                    hazard_addressed=hazard,
                    standard_ref=req.get('standard_ref'),
                    confidence=0.95,
                ))

        # Numerical data
        for key, value in data.get('numerical_data', {}).items():
            facts.append(Fact(
                type=FactType.NUMERICAL_THRESHOLD,
                claim=f"{key.replace('_', ' ')}: {value}",
                numerical_value=str(value),
                category=key.replace('_', ' '),
                confidence=0.95,
            ))

        # Compliance obligations (from regulatory_references)
        for ref in data.get('regulatory_references', []):
            if ref.get('description'):
                facts.append(Fact(
                    type=FactType.COMPLIANCE_OBLIGATION,
                    claim=ref['description'],
                    standard_ref=ref.get('id'),
                    confidence=0.90,
                ))

        return facts

    # ------------------------------------------------------------------
    # Regulatory references
    # ------------------------------------------------------------------

    def _extract_regulatory_refs(self, data: Dict) -> List[RegulatoryReference]:
        return [
            RegulatoryReference(
                id=ref.get('id', ''),
                title=ref.get('title', ''),
                description=ref.get('description'),
                sections=ref.get('sections', []),
            )
            for ref in data.get('regulatory_references', [])
        ]

    # ------------------------------------------------------------------
    # Chunk parsing
    # ------------------------------------------------------------------

    def _parse_chunk(self, content: str, source_path: Path) -> ProcessedDocument:
        chunk_meta = self._extract_chunk_metadata(content)
        content_text = self._extract_content_section(content)
        doc_id = source_path.stem

        return ProcessedDocument(
            document_id=doc_id,
            document_type=DocumentType.PDF_CHUNK,
            source_authority=0.80,
            relevance_score=0.0,  # will be computed by DomainModel
            processed_at=datetime.now(),
            text_snippet=content_text[:1000],
            full_text_hash=ProcessedDocument.compute_text_hash(content_text),
            chunk_metadata=chunk_meta,
            extraction_confidence=0.70,  # lower — needs further processing
            flags=['pdf_chunk', 'needs_extraction'],
        )

    def _extract_chunk_metadata(self, content: str) -> ChunkMetadata:
        meta_match = re.search(
            r'## chunk_metadata\s*\n(.*?)(?=\n## |\Z)',
            content, re.DOTALL,
        )
        if not meta_match:
            return ChunkMetadata()

        meta_text = meta_match.group(1)
        chunk_id = self._extract_field(meta_text, 'chunk_id')
        section = self._extract_field(meta_text, 'section')
        token_est = self._extract_field(meta_text, 'token_estimate')
        section_title = self._extract_field(meta_text, 'section_title')
        prev_chunk = self._extract_field(meta_text, 'prev_chunk')
        next_chunk = self._extract_field(meta_text, 'next_chunk')
        context_overlap = self._extract_context_overlap(content)

        return ChunkMetadata(
            chunk_id=chunk_id,
            section=section,
            token_estimate=int(token_est) if token_est.isdigit() else 0,
            key_entities=self._extract_list_field(meta_text, 'key_entities'),
            key_concepts=self._extract_list_field(meta_text, 'key_concepts'),
            section_numbers=self._extract_list_field(meta_text, 'section_numbers'),
            section_title=section_title,
            prev_chunk=prev_chunk,
            next_chunk=next_chunk,
            cross_references=self._extract_list_field(meta_text, 'cross_references'),
            context_overlap=context_overlap,
            figures=[
                FigureMetadata(figure_id=fid)
                for fid in self._extract_list_field(meta_text, 'figures')
            ],
            tables=[
                TableData(table_id=tid)
                for tid in self._extract_list_field(meta_text, 'tables')
            ],
        )

    def _extract_content_section(self, content: str) -> str:
        content_match = re.search(
            r'## content\s*\n(.*?)(?=\n## |\Z)',
            content, re.DOTALL,
        )
        if content_match:
            return content_match.group(1).strip()

        # Fallback: everything after chunk_metadata
        meta_end = re.search(r'## chunk_metadata.*?(?=\n## )', content, re.DOTALL)
        if meta_end:
            return content[meta_end.end():].strip()

        return content.strip()

    @staticmethod
    def _extract_context_overlap(content: str) -> str:
        """Parse the ``## context_overlap`` section (v2 chunk format)."""
        overlap_match = re.search(
            r'## context_overlap\s*\n(.*?)(?=\n## |\Z)',
            content, re.DOTALL,
        )
        return overlap_match.group(1).strip() if overlap_match else ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_field(text: str, field_name: str) -> str:
        match = re.search(rf'-\s*{field_name}:\s*(.+)', text)
        return match.group(1).strip() if match else ''

    @staticmethod
    def _extract_list_field(text: str, field_name: str) -> List[str]:
        match = re.search(rf'-\s*{field_name}:\s*\[([^\]]*)\]', text)
        if not match:
            return []
        items = match.group(1).split(',')
        return [item.strip().strip('"\'') for item in items if item.strip()]

    def _map_doc_type(self, doc_type: str) -> DocumentType:
        return self.DOC_TYPE_MAP.get(doc_type.lower(), DocumentType.PDF_STRUCTURED)

    def _get_authority(self, publisher: str) -> float:
        publisher_lower = publisher.lower()
        for key, authority in self.AUTHORITY_MAP.items():
            if key in publisher_lower:
                return authority
        return self._DEFAULT_AUTHORITY
