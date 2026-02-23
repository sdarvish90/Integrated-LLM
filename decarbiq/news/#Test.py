#Test

from nlp.document_processor import DocumentProcessor
from nlp.entity_extractor import EntityExtractor
from nlp.schema import DocumentType, EntityType

# === Document Processor Tests ===

processor = DocumentProcessor()

# Test news article
news_result = processor.process(
    "Air Products announced today that it has reached final investment decision on a $4.5 billion blue hydrogen project in Louisiana.",
    document_type=DocumentType.NEWS
)
assert news_result is not None
assert any(e.value == "Air Products" for e in news_result.entities)
assert any(f.type.value == "status_change" for f in news_result.facts)

# Test SEC filing
sec_result = processor.process(
    "Item 1.01 Entry into Material Agreement. The Company entered into an EPC contract with Bechtel for construction of a 1.5 MTPA ammonia facility.",
    document_type=DocumentType.SEC_FILING
)
assert sec_result is not None
assert sec_result.source_authority > 0.9

# Test irrelevant document (should return None)
irrelevant = processor.process(
    "Hydrogen bonding plays a key role in DNA structure.",
    document_type=DocumentType.NEWS
)
assert irrelevant is None

# === Entity Extractor Tests ===

extractor = EntityExtractor()

# Test basic extraction
entities = extractor.extract("Air Products announced a facility in Louisiana")
assert len(entities) > 0
assert any(e.type == EntityType.COMPANY for e in entities)

# Test chunk file extraction
entities, chunk_meta = extractor.extract_from_chunk(
    "anhydrous_ammonia_fertilizer_distribution_001_maintenance.md"
)
assert chunk_meta is not None
assert chunk_meta.section == "maintenance_mechanical_integrity"
assert "EPA" in chunk_meta.key_entities or any("EPA" in e.value for e in entities)
assert len(entities) > 0

# Test seeds file extraction
seeds_result = extractor.extract_from_seeds_yaml(
    "anhydrous_ammonia_fertilizer_distribution_seeds.yaml"
)
assert 'positive_entities' in seeds_result
assert 'negative_entities' in seeds_result
assert len(seeds_result['positive_entities']) > 0

# Test backward compatibility
entity_dicts = extractor.extract_to_dict("CF Industries in Louisiana")
assert isinstance(entity_dicts, list)
assert all(isinstance(e, dict) for e in entity_dicts)

print("All tests passed!")