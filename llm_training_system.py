#!/usr/bin/env python3
"""
LLM Training & RAG System for TEA Intelligence
===============================================

This system:
1. Ingests documents from your training folder (PDFs, Excel, Word, text)
2. Builds a vector database (embeddings) for semantic search
3. Trains/fine-tunes LLM on your TEA data
4. Provides RAG (Retrieval-Augmented Generation) for news analysis
5. Quantifies impact on your baseline TEA numbers

Usage:
    # Step 1: Ingest training data
    python llm_training_system.py --mode ingest --folder /path/to/training/data
    
    # Step 2: Build knowledge base
    python llm_training_system.py --mode build_kb
    
    # Step 3: Analyze news impact
    python llm_training_system.py --mode analyze --news-db hydrogen_intelligence_v2.db
"""

import os
import sys
import argparse
import sqlite3
import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

# Document processing
import PyPDF2
import pandas as pd
from docx import Document as DocxDocument
import openpyxl

# Vector embeddings
from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings
import numpy as np

# LLM
import anthropic

# TEA baseline
from tea_baseline_adapter import ShareRunConfig, ShareBaselineAdapter

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    'training_folder': None,  # Set via command line
    'vector_db_path': './knowledge_base_db',
    'embeddings_model': 'all-MiniLM-L6-v2',  # Fast, good quality
    'anthropic_api_key': os.environ.get('ANTHROPIC_API_KEY'),
    'chunk_size': 1000,  # Characters per chunk
    'chunk_overlap': 200,  # Overlap between chunks
    'max_context_documents': 5,  # Top K documents for RAG
}

# ============================================================================
# DOCUMENT INGESTION
# ============================================================================

class DocumentIngester:
    """
    Ingest documents from training folder
    
    Supported formats:
    - PDF (.pdf)
    - Word (.docx)
    - Excel (.xlsx, .xls, .csv)
    - Text (.txt, .md)
    - JSON (.json)
    """
    
    def __init__(self, training_folder: Path):
        self.training_folder = Path(training_folder)
        self.documents = []
    
    MANIFEST_FILE = "ingest_manifest.json"

    def _file_fingerprint(self, file_path: Path) -> str:
        """Return a fingerprint string for a file based on its size and mtime."""
        stat = file_path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def _load_manifest(self) -> dict:
        """Load the ingest manifest (path -> fingerprint mapping)."""
        manifest_path = Path(self.MANIFEST_FILE)
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_manifest(self, manifest: dict):
        """Save the ingest manifest to disk."""
        with open(self.MANIFEST_FILE, 'w') as f:
            json.dump(manifest, f, indent=2)

    def ingest_all(self):
        """
        Ingest all documents from training folder.
        Uses a manifest file to skip files that have already been processed.
        """
        print(f"\n{'='*70}")
        print(f"INGESTING DOCUMENTS FROM: {self.training_folder}")
        print(f"{'='*70}\n")

        if not self.training_folder.exists():
            raise FileNotFoundError(f"Training folder not found: {self.training_folder}")

        # Find all supported files
        supported_extensions = ['.pdf', '.docx', '.xlsx', '.xls', '.csv', '.txt', '.md', '.json']
        all_files = []

        for ext in supported_extensions:
            all_files.extend(self.training_folder.rglob(f'*{ext}'))

        # --- Manifest-based filtering: skip already-ingested files ---
        manifest = self._load_manifest()
        files = []
        skipped = 0
        for fp in all_files:
            key = str(fp.resolve())
            fingerprint = self._file_fingerprint(fp)
            if manifest.get(key) == fingerprint:
                skipped += 1
            else:
                files.append(fp)

        print(f"Found {len(all_files)} documents total")
        if skipped:
            print(f"Skipping {skipped} already-ingested files (unchanged since last run)")
        print(f"Processing {len(files)} new/modified files\n")

        if not files:
            print("Nothing new to ingest.")
            return self.documents

        # Process each file (only new/modified)
        for file_path in files:
            try:
                print(f"Processing: {file_path.name}...")
                
                if file_path.suffix == '.pdf':
                    docs = self._ingest_pdf(file_path)
                elif file_path.suffix == '.docx':
                    docs = self._ingest_docx(file_path)
                elif file_path.suffix in ['.xlsx', '.xls', '.csv']:
                    docs = self._ingest_excel(file_path)
                elif file_path.suffix in ['.txt', '.md']:
                    docs = self._ingest_text(file_path)
                elif file_path.suffix == '.json':
                    docs = self._ingest_json(file_path)
                else:
                    continue
                
                self.documents.extend(docs)
                print(f"  ✓ Extracted {len(docs)} chunks\n")
                
            except Exception as e:
                print(f"  ✗ Error: {str(e)}\n")
                continue
        
        # Update manifest with newly processed files
        for fp in files:
            key = str(fp.resolve())
            try:
                manifest[key] = self._file_fingerprint(fp)
            except OSError:
                pass  # file may have been removed during processing
        self._save_manifest(manifest)

        print(f"{'='*70}")
        print(f"INGESTION COMPLETE: {len(self.documents)} new chunks from {len(files)} files")
        print(f"{'='*70}\n")

        return self.documents
    
    def _ingest_pdf(self, file_path: Path) -> List[Dict]:
        """Extract text from PDF"""
        docs = []
        
        try:
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                
                full_text = ""
                for page_num, page in enumerate(reader.pages):
                    text = page.extract_text()
                    full_text += text + "\n"
                
                # Chunk the text
                chunks = self._chunk_text(full_text, CONFIG['chunk_size'], CONFIG['chunk_overlap'])
                
                for i, chunk in enumerate(chunks):
                    docs.append({
                        'text': chunk,
                        'source': file_path.name,
                        'source_type': 'pdf',
                        'chunk_id': i,
                        'metadata': {
                            'file_path': str(file_path),
                            'total_pages': len(reader.pages),
                            'chunk_index': i,
                            'total_chunks': len(chunks)
                        }
                    })
        
        except Exception as e:
            raise Exception(f"PDF extraction failed: {str(e)}")
        
        return docs
    
    def _ingest_docx(self, file_path: Path) -> List[Dict]:
        """Extract text from Word document"""
        docs = []
        
        try:
            doc = DocxDocument(file_path)
            
            full_text = "\n".join([para.text for para in doc.paragraphs])
            
            chunks = self._chunk_text(full_text, CONFIG['chunk_size'], CONFIG['chunk_overlap'])
            
            for i, chunk in enumerate(chunks):
                docs.append({
                    'text': chunk,
                    'source': file_path.name,
                    'source_type': 'docx',
                    'chunk_id': i,
                    'metadata': {
                        'file_path': str(file_path),
                        'chunk_index': i,
                        'total_chunks': len(chunks)
                    }
                })
        
        except Exception as e:
            raise Exception(f"DOCX extraction failed: {str(e)}")
        
        return docs
    
    def _ingest_excel(self, file_path: Path) -> List[Dict]:
        """Extract data from Excel/CSV"""
        docs = []
        
        try:
            # Read Excel/CSV
            if file_path.suffix == '.csv':
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            
            # Convert to text representation
            text = f"Table from {file_path.name}:\n\n"
            text += df.to_string(index=False)
            
            # Also create structured representation
            text += "\n\nStructured data:\n"
            text += json.dumps(df.to_dict('records'), indent=2)
            
            chunks = self._chunk_text(text, CONFIG['chunk_size'] * 2, CONFIG['chunk_overlap'])
            
            for i, chunk in enumerate(chunks):
                docs.append({
                    'text': chunk,
                    'source': file_path.name,
                    'source_type': 'excel',
                    'chunk_id': i,
                    'metadata': {
                        'file_path': str(file_path),
                        'rows': len(df),
                        'columns': list(df.columns),
                        'chunk_index': i,
                        'total_chunks': len(chunks)
                    }
                })
        
        except Exception as e:
            raise Exception(f"Excel extraction failed: {str(e)}")
        
        return docs
    
    def _ingest_text(self, file_path: Path) -> List[Dict]:
        """Extract text from text file"""
        docs = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            
            chunks = self._chunk_text(text, CONFIG['chunk_size'], CONFIG['chunk_overlap'])
            
            for i, chunk in enumerate(chunks):
                docs.append({
                    'text': chunk,
                    'source': file_path.name,
                    'source_type': 'text',
                    'chunk_id': i,
                    'metadata': {
                        'file_path': str(file_path),
                        'chunk_index': i,
                        'total_chunks': len(chunks)
                    }
                })
        
        except Exception as e:
            raise Exception(f"Text extraction failed: {str(e)}")
        
        return docs
    
    def _ingest_json(self, file_path: Path) -> List[Dict]:
        """Extract data from JSON"""
        docs = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Convert JSON to readable text
            text = f"Data from {file_path.name}:\n\n"
            text += json.dumps(data, indent=2)
            
            chunks = self._chunk_text(text, CONFIG['chunk_size'], CONFIG['chunk_overlap'])
            
            for i, chunk in enumerate(chunks):
                docs.append({
                    'text': chunk,
                    'source': file_path.name,
                    'source_type': 'json',
                    'chunk_id': i,
                    'metadata': {
                        'file_path': str(file_path),
                        'chunk_index': i,
                        'total_chunks': len(chunks)
                    }
                })
        
        except Exception as e:
            raise Exception(f"JSON extraction failed: {str(e)}")
        
        return docs
    
    def _chunk_text(self, text: str, chunk_size: int, overlap: int) -> List[str]:
        """
        Split text into overlapping chunks
        """
        if len(text) <= chunk_size:
            return [text]
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            # Try to break at sentence boundary
            if end < len(text):
                # Look for sentence ending
                for punct in ['. ', '.\n', '! ', '?\n']:
                    last_punct = text[start:end].rfind(punct)
                    if last_punct > chunk_size * 0.5:  # Don't break too early
                        end = start + last_punct + len(punct)
                        break
            
            chunks.append(text[start:end].strip())
            start = end - overlap
        
        return chunks

# ============================================================================
# VECTOR DATABASE (KNOWLEDGE BASE)
# ============================================================================

class KnowledgeBase:
    """
    Vector database for semantic search over training documents
    
    Uses ChromaDB + SentenceTransformers for embeddings
    """
    
    def __init__(self, db_path: str = CONFIG['vector_db_path']):
        self.db_path = db_path
        
        # Initialize ChromaDB (new API)
        self.client = chromadb.PersistentClient(path=db_path)
        
        # Create or get collection
        self.collection = self.client.get_or_create_collection(
            name="tea_knowledge",
            metadata={"description": "TEA training documents"}
        )
        
        # Initialize embedding model
        print("Loading embedding model...")
        self.embedder = SentenceTransformer(CONFIG['embeddings_model'])
        print("✓ Embedding model loaded\n")
    
    def build(self, documents: List[Dict]):
        """
        Build knowledge base from documents
        """
        print(f"\n{'='*70}")
        print(f"BUILDING KNOWLEDGE BASE")
        print(f"{'='*70}\n")
        
        print(f"Documents to process: {len(documents)}")

        # Check which documents already exist in the collection (for resume capability)
        print("Checking for existing documents...")
        existing_ids = set()
        try:
            # Get all existing IDs from the collection
            existing_data = self.collection.get()
            if existing_data and existing_data['ids']:
                existing_ids = set(existing_data['ids'])
            print(f"Found {len(existing_ids)} existing documents in knowledge base")
        except Exception as e:
            print(f"Could not check existing documents: {e}")

        # Filter out documents that already exist
        if existing_ids:
            documents = [doc for doc in documents
                        if f"{doc['source']}_{doc['chunk_id']}" not in existing_ids]
            print(f"Documents remaining after filtering: {len(documents)}")

        if not documents:
            print("All documents already in knowledge base. Nothing to add.")
            return

        print(f"Generating embeddings...\n")

        # Process in batches
        batch_size = 100
        total_added = 0

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i+batch_size]
            
            # Generate embeddings
            texts = [doc['text'] for doc in batch]
            embeddings = self.embedder.encode(texts, show_progress_bar=False).tolist()
            
            # Prepare for ChromaDB
            ids = [f"{doc['source']}_{doc['chunk_id']}" for doc in batch]

            # Sanitize metadata - ChromaDB only accepts str, int, float, bool, or None
            def sanitize_metadata(meta_dict):
                sanitized = {}
                for k, v in meta_dict.items():
                    if v is None or isinstance(v, (str, int, float, bool)):
                        sanitized[k] = v
                    elif isinstance(v, (list, tuple)):
                        sanitized[k] = json.dumps(v)  # Convert lists to JSON string
                    elif isinstance(v, dict):
                        sanitized[k] = json.dumps(v)  # Convert dicts to JSON string
                    else:
                        sanitized[k] = str(v)  # Convert anything else to string
                return sanitized

            metadatas = [sanitize_metadata({
                'source': doc['source'],
                'source_type': doc['source_type'],
                'chunk_id': doc['chunk_id'],
                **doc['metadata']
            }) for doc in batch]
            
            # Add to collection (upsert to handle resume/duplicates)
            self.collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas
            )
            
            total_added += len(batch)
            print(f"  Progress: {total_added}/{len(documents)} documents")
        
        print(f"\n{'='*70}")
        print(f"✅ KNOWLEDGE BASE BUILT: {total_added} document chunks")
        print(f"{'='*70}\n")
    
    def search(self, query: str, top_k: int = CONFIG['max_context_documents'], 
               filters: Optional[Dict] = None) -> List[Dict]:
        """
        Semantic search over knowledge base
        
        Args:
            query: Search query
            top_k: Number of results to return
            filters: Metadata filters (e.g., {'source_type': 'pdf'})
        
        Returns:
            List of documents with similarity scores
        """
        # Generate query embedding
        query_embedding = self.embedder.encode(query).tolist()
        
        # Search
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=filters
        )
        
        # Format results
        documents = []
        for i in range(len(results['ids'][0])):
            documents.append({
                'id': results['ids'][0][i],
                'text': results['documents'][0][i],
                'metadata': results['metadatas'][0][i],
                'distance': results['distances'][0][i] if 'distances' in results else None,
                'similarity': 1 - results['distances'][0][i] if 'distances' in results else None
            })
        
        return documents

# ============================================================================
# LLM-POWERED NEWS ANALYZER (RAG)
# ============================================================================

class NewsImpactAnalyzer:
    """
    Analyze news articles and quantify impact on TEA baseline
    
    Uses RAG (Retrieval-Augmented Generation):
    1. Retrieve relevant context from knowledge base
    2. Use LLM to extract structured impact
    3. Quantify impact on baseline TEA numbers
    """
    
    def __init__(self, knowledge_base: KnowledgeBase, tea_baselines: Dict):
        self.kb = knowledge_base
        self.tea_baselines = tea_baselines
        
        # Initialize LLM
        if not CONFIG['anthropic_api_key']:
            raise ValueError("ANTHROPIC_API_KEY not set. Please set environment variable.")
        
        self.llm = anthropic.Anthropic(api_key=CONFIG['anthropic_api_key'])
    
    def analyze_article(self, article: Dict) -> Dict:
        """
        Analyze single news article
        
        Args:
            article: {
                'title': str,
                'snippet': str,
                'url': str,
                'category': str,
                'region': str
            }
        
        Returns:
            {
                'impact_type': str,
                'affected_parameters': {...},
                'baseline_changes': {...},
                'confidence': float,
                'reasoning': str
            }
        """
        print(f"\nAnalyzing: {article['title'][:60]}...")
        
        # Step 1: Retrieve relevant context from knowledge base
        query = f"{article['title']} {article.get('snippet', '')}"
        context_docs = self.kb.search(query, top_k=5)
        
        # Step 2: Build prompt with context
        prompt = self._build_analysis_prompt(article, context_docs)
        
        # Step 3: Call LLM
        try:
            response = self.llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse response
            text = response.content[0].text
            
            # Extract JSON
            if "```json" in text:
                json_text = text.split("```json")[1].split("```")[0]
            else:
                json_text = text
            
            impact = json.loads(json_text)
            
            # Step 4: Quantify impact on baseline
            impact['baseline_changes'] = self._quantify_baseline_impact(impact, article.get('region'))
            
            print(f"  ✓ Impact type: {impact.get('impact_type', 'unknown')}")
            print(f"  ✓ Confidence: {impact.get('confidence', 0):.0%}")
            
            return impact
            
        except Exception as e:
            print(f"  ✗ Analysis failed: {str(e)}")
            return {
                'impact_type': 'unknown',
                'affected_parameters': {},
                'baseline_changes': {},
                'confidence': 0,
                'reasoning': f"Analysis failed: {str(e)}"
            }
    
    def _build_analysis_prompt(self, article: Dict, context_docs: List[Dict]) -> str:
        """
        Build RAG prompt with retrieved context
        """
        # Format context
        context_text = "\n\n".join([
            f"[Source: {doc['metadata']['source']}]\n{doc['text']}"
            for doc in context_docs
        ])
        
        prompt = f"""You are an expert energy economist analyzing news impact on hydrogen project economics.

CONTEXT FROM KNOWLEDGE BASE (Your Training Data):
{context_text}

NEWS ARTICLE TO ANALYZE:
Title: {article['title']}
Summary: {article.get('snippet', 'N/A')}
Category: {article.get('category', 'N/A')}
Region: {article.get('region', 'N/A')}
URL: {article.get('url', 'N/A')}

TASK:
Analyze how this news impacts green hydrogen project economics. Extract structured impact in JSON format:

{{
    "impact_type": "subsidy_announcement|policy_change|technology_breakthrough|cost_reduction|demand_increase|supply_chain|regulatory_change|competitive_development|market_shift|other",
    
    "affected_parameters": {{
        "parameter_name": {{
            "current_value": <number>,
            "new_value": <number>,
            "change_percent": <number>,
            "confidence": <0.0-1.0>
        }}
    }},
    
    "impact_magnitude": "negligible|low|moderate|high|critical",
    
    "time_horizon": "immediate|short_term|medium_term|long_term",
    
    "affected_regions": ["list of regions"],
    
    "reasoning": "Detailed explanation of how this affects project economics, citing the knowledge base context where relevant",
    
    "confidence": <0.0-1.0>
}}

PARAMETERS TO CONSIDER:
- electricity_price_usd_per_mwh: Cost of renewable electricity
- electrolyzer_capex_usd_per_kw: Electrolyzer capital cost
- electrolyzer_efficiency_kwh_per_kg: Energy consumption per kg H2
- capacity_factor_percent: Plant utilization rate
- opex_percent_of_capex: Operating cost as % of CAPEX
- offtake_price_usd_per_kg: Hydrogen selling price
- subsidy_rate_percent: Government subsidy/tax credit
- project_timeline_months: Time to FID/construction
- demand_growth_percent: Market demand growth rate

IMPORTANT:
- Use the CONTEXT FROM KNOWLEDGE BASE to ground your analysis
- Be CONSERVATIVE with estimates (don't exaggerate impact)
- Only include parameters with CLEAR causal links
- Provide SPECIFIC numeric estimates when possible
- Express UNCERTAINTY honestly (don't pretend certainty)
- Consider SECOND-ORDER effects (e.g., subsidy → competition → lower margins)

Return ONLY valid JSON, no markdown formatting.
"""
        
        return prompt
    
    def _quantify_baseline_impact(self, impact: Dict, region: Optional[str]) -> Dict:
        """
        Quantify impact on baseline TEA numbers
        
        Uses simplified TEA model to estimate NPV, LCOH changes
        """
        if not region or region not in self.tea_baselines:
            return {
                'npv_change_usd_million': 0,
                'lcoh_change_usd_per_kg': 0,
                'irr_change_percent': 0,
                'note': 'Region not specified or baseline not available'
            }
        
        baseline = self.tea_baselines[region]
        affected_params = impact.get('affected_parameters', {})
        
        # Simple sensitivity analysis
        # In reality, you'd run full TEA model with updated parameters
        
        changes = {
            'npv_change_usd_million': 0,
            'lcoh_change_usd_per_kg': 0,
            'irr_change_percent': 0
        }
        
        # Electricity price impact on LCOH
        if 'electricity_price_usd_per_mwh' in affected_params:
            param = affected_params['electricity_price_usd_per_mwh']
            price_change = param['new_value'] - param['current_value']
            confidence = param.get('confidence', 0.5)
            
            # Rule of thumb: $10/MWh change → $0.50/kg LCOH change
            lcoh_change = (price_change / 10) * 0.50 * confidence
            changes['lcoh_change_usd_per_kg'] += lcoh_change
            
            # LCOH impact on NPV (simplified)
            # Assume 10k tonnes/year, 20 year project
            production_tonnes = 10000 * 20
            npv_change = -lcoh_change * production_tonnes * confidence
            changes['npv_change_usd_million'] += npv_change
        
        # Electrolyzer CAPEX impact
        if 'electrolyzer_capex_usd_per_kw' in affected_params:
            param = affected_params['electrolyzer_capex_usd_per_kw']
            capex_change_percent = param.get('change_percent', 0) / 100
            confidence = param.get('confidence', 0.5)
            
            # Impact on total CAPEX
            baseline_capex = baseline.get('capex', 400)  # $M
            capex_change = baseline_capex * capex_change_percent * confidence
            
            # CAPEX impact on NPV (1:1 approximately)
            changes['npv_change_usd_million'] -= capex_change
            
            # CAPEX impact on LCOH
            # Rule of thumb: 10% CAPEX change → $0.10/kg LCOH change
            lcoh_change = (capex_change_percent * 10) * 0.10 * confidence
            changes['lcoh_change_usd_per_kg'] += lcoh_change
        
        # Subsidy impact
        if 'subsidy_rate_percent' in affected_params:
            param = affected_params['subsidy_rate_percent']
            subsidy_rate = param.get('new_value', 0) / 100
            confidence = param.get('confidence', 0.5)
            
            baseline_lcoh = baseline.get('lcoh', 1.90)
            
            # Subsidy reduces effective LCOH
            lcoh_reduction = baseline_lcoh * subsidy_rate * confidence
            changes['lcoh_change_usd_per_kg'] -= lcoh_reduction
            
            # NPV impact
            production_tonnes = 10000 * 20
            npv_increase = lcoh_reduction * production_tonnes * confidence
            changes['npv_change_usd_million'] += npv_increase
        
        # Offtake price impact
        if 'offtake_price_usd_per_kg' in affected_params:
            param = affected_params['offtake_price_usd_per_kg']
            price_change = param['new_value'] - param['current_value']
            confidence = param.get('confidence', 0.5)
            
            # Direct impact on NPV
            production_tonnes = 10000 * 20
            npv_change = price_change * production_tonnes * confidence
            changes['npv_change_usd_million'] += npv_change
        
        # IRR approximation
        # Simple: NPV change / baseline NPV → proportional IRR change
        if baseline.get('npv', 0) > 0:
            npv_change_percent = changes['npv_change_usd_million'] / baseline['npv']
            baseline_irr = baseline.get('irr', 10)
            changes['irr_change_percent'] = npv_change_percent * baseline_irr
        
        return changes
    
    def analyze_recent_articles(self, news_db_path: str, days_back: int = 7, 
                                min_category_score: int = 5) -> Dict:
        """
        Analyze recent articles from news database
        
        Args:
            news_db_path: Path to hydrogen_intelligence_v2.db
            days_back: How many days back to analyze
            min_category_score: Minimum priority score (5 = HIGH+)
        
        Returns:
            Summary with cumulative impact analysis
        """
        print(f"\n{'='*70}")
        print(f"ANALYZING RECENT NEWS (Last {days_back} days)")
        print(f"{'='*70}\n")
        
        # Load articles from database
        conn = sqlite3.connect(news_db_path)
        cursor = conn.cursor()
        
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        cursor.execute('''
            SELECT title, snippet, url, category, region, priority_score, published_date
            FROM articles
            WHERE fetched_date >= ?
            AND priority_score >= ?
            ORDER BY priority_score DESC
        ''', (cutoff_date, min_category_score))
        
        articles = cursor.fetchall()
        conn.close()
        
        print(f"Found {len(articles)} relevant articles\n")
        
        # Analyze each article
        results = []
        
        for i, article_row in enumerate(articles, 1):
            title, snippet, url, category, region, score, pub_date = article_row
            
            article = {
                'title': title,
                'snippet': snippet or '',
                'url': url,
                'category': category,
                'region': region,
                'priority_score': score
            }
            
            print(f"[{i}/{len(articles)}] Priority: {score} | Category: {category}")
            
            impact = self.analyze_article(article)
            impact['article'] = article
            
            results.append(impact)
        
        # Aggregate results by region
        print(f"\n{'='*70}")
        print(f"CUMULATIVE IMPACT ANALYSIS")
        print(f"{'='*70}\n")
        
        summary = self._aggregate_impacts(results)
        
        return summary
    
    def _aggregate_impacts(self, results: List[Dict]) -> Dict:
        """
        Aggregate impacts by region
        """
        by_region = {}
        
        for result in results:
            article = result.get('article', {})
            region = article.get('region')
            
            if not region or region not in self.tea_baselines:
                continue
            
            if region not in by_region:
                by_region[region] = {
                    'articles_analyzed': 0,
                    'total_npv_change': 0,
                    'total_lcoh_change': 0,
                    'total_irr_change': 0,
                    'high_confidence_count': 0,
                    'impacts': []
                }
            
            changes = result.get('baseline_changes', {})
            confidence = result.get('confidence', 0)
            
            by_region[region]['articles_analyzed'] += 1
            by_region[region]['total_npv_change'] += changes.get('npv_change_usd_million', 0)
            by_region[region]['total_lcoh_change'] += changes.get('lcoh_change_usd_per_kg', 0)
            by_region[region]['total_irr_change'] += changes.get('irr_change_percent', 0)
            
            if confidence > 0.7:
                by_region[region]['high_confidence_count'] += 1
            
            by_region[region]['impacts'].append(result)
        
        # Generate recommendations
        for region, data in by_region.items():
            baseline = self.tea_baselines[region]
            
            npv_change_percent = (data['total_npv_change'] / baseline['npv']) * 100
            lcoh_change_percent = (data['total_lcoh_change'] / baseline['lcoh']) * 100
            
            # Decision logic
            if npv_change_percent > 15 and data['high_confidence_count'] >= 2:
                recommendation = "ACCELERATE"
                urgency = "HIGH"
            elif npv_change_percent > 5:
                recommendation = "PROCEED"
                urgency = "MEDIUM"
            elif npv_change_percent < -10 and data['high_confidence_count'] >= 2:
                recommendation = "RECONSIDER"
                urgency = "HIGH"
            else:
                recommendation = "MONITOR"
                urgency = "LOW"
            
            data['recommendation'] = recommendation
            data['urgency'] = urgency
            data['npv_change_percent'] = npv_change_percent
            data['lcoh_change_percent'] = lcoh_change_percent
            
            # Print summary
            print(f"{'='*70}")
            print(f"REGION: {region}")
            print(f"{'='*70}")
            print(f"Baseline NPV: ${baseline['npv']:.1f}M | LCOH: ${baseline['lcoh']:.2f}/kg")
            print(f"\nNews Analysis ({data['articles_analyzed']} articles):")
            print(f"  Total NPV impact: ${data['total_npv_change']:+.1f}M ({npv_change_percent:+.1f}%)")
            print(f"  Total LCOH impact: ${data['total_lcoh_change']:+.3f}/kg ({lcoh_change_percent:+.1f}%)")
            print(f"  Total IRR impact: {data['total_irr_change']:+.2f}%")
            print(f"  High confidence findings: {data['high_confidence_count']}")
            print(f"\n🎯 RECOMMENDATION: {recommendation} (Urgency: {urgency})")
            print()
        
        return {
            'by_region': by_region,
            'total_articles': len(results)
        }

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='LLM Training & RAG System for TEA Intelligence',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Step 1: Ingest training documents
  python llm_training_system.py --mode ingest --folder /path/to/training/data
  
  # Step 2: Build knowledge base
  python llm_training_system.py --mode build_kb
  
  # Step 3: Test knowledge base search
  python llm_training_system.py --mode search --query "What is typical LCOH for Oman?"
  
  # Step 4: Analyze recent news
  python llm_training_system.py --mode analyze --news-db hydrogen_intelligence_v2.db --days 7
        """
    )
    
    parser.add_argument('--mode', required=True,
                       choices=['ingest', 'build_kb', 'search', 'analyze', 'all'],
                       help='Operation mode (use "all" to run full pipeline)')
    
    parser.add_argument('--folder', help='Path to training data folder (for ingest mode)')
    parser.add_argument('--news-db', help='Path to news database (for analyze mode)')
    parser.add_argument('--days', type=int, default=7, help='Days back to analyze (default: 7)')
    parser.add_argument('--query', help='Search query (for search mode)')
    parser.add_argument('--output', help='Output file for results (JSON)')
    
    args = parser.parse_args()
    
    # Load TEA baselines
    print("\nLoading TEA baselines...")
    tea_cfg = ShareRunConfig(
        locations_root=Path("/Users/Shadi/Dropbox/SHARE_Model_LLM"),
        share_entrypoint="SHARE_Model_main_v1.py",
        baseline_output_name="baseline.json",
        share_output_hint="outputs.json",
    )
    
    tea_baselines = {}
    location_map = {
        'Duqm_Oman': 'Oman',
        'Magallines_Chile': 'Chile',
        'Houston_USA': 'Houston'
    }
    
    for location_folder, region_name in location_map.items():
        try:
            tea = ShareBaselineAdapter(tea_cfg)
            baseline_data = tea.get_baseline(location_folder)
            
            if baseline_data:
                tea_baselines[region_name] = {
                    'npv': baseline_data.get('npv', 580),
                    'lcoh': baseline_data.get('lcoh_usd_per_kg', 1.90),
                    'irr': baseline_data.get('irr_pct', 10),
                    'capex': baseline_data.get('capex', 400)
                }
                print(f"  ✓ {region_name}: NPV=${tea_baselines[region_name]['npv']:.1f}M, LCOH=${tea_baselines[region_name]['lcoh']:.2f}/kg")
        except:
            # Use defaults
            tea_baselines[region_name] = {
                'npv': 580, 'lcoh': 1.90, 'irr': 10, 'capex': 400
            }
    
    # Execute based on mode
    if args.mode == 'ingest':
        if not args.folder:
            print("Error: --folder required for ingest mode")
            sys.exit(1)
        
        # Ingest documents (only new/modified files)
        ingester = DocumentIngester(args.folder)
        new_documents = ingester.ingest_all()

        # Save to ingested_documents.json, replacing chunks from re-processed sources
        output_file = 'ingested_documents.json'
        if new_documents:
            existing_documents = []
            if Path(output_file).exists():
                try:
                    with open(output_file, 'r') as f:
                        existing_documents = json.load(f)
                    print(f"Loaded {len(existing_documents)} existing chunks from {output_file}")
                except (json.JSONDecodeError, IOError):
                    existing_documents = []

            # Remove old chunks for any source that was re-processed
            new_sources = {doc.get('source', '') for doc in new_documents}
            kept = [doc for doc in existing_documents if doc.get('source', '') not in new_sources]
            combined = kept + new_documents
            with open(output_file, 'w') as f:
                json.dump(combined, f, indent=2)
            print(f"✅ Saved {len(combined)} total chunks ({len(new_documents)} new, {len(kept)} kept) to: {output_file}")
        else:
            print(f"✅ No new files to process. {output_file} unchanged.")

        print(f"\nNext step: python llm_training_system.py --mode build_kb")
    
    elif args.mode == 'build_kb':
        # Load ingested documents
        with open('ingested_documents.json', 'r') as f:
            documents = json.load(f)
        
        # Build knowledge base
        kb = KnowledgeBase()
        kb.build(documents)
        
        print(f"\nNext step: python llm_training_system.py --mode analyze --news-db hydrogen_intelligence_v2.db")
    
    elif args.mode == 'search':
        if not args.query:
            print("Error: --query required for search mode")
            sys.exit(1)
        
        # Search knowledge base
        kb = KnowledgeBase()
        results = kb.search(args.query, top_k=5)
        
        print(f"\n{'='*70}")
        print(f"SEARCH RESULTS FOR: {args.query}")
        print(f"{'='*70}\n")
        
        for i, doc in enumerate(results, 1):
            print(f"{i}. [Source: {doc['metadata']['source']}] (Similarity: {doc['similarity']:.2%})")
            print(f"   {doc['text'][:200]}...")
            print()
    
    elif args.mode == 'analyze':
        if not args.news_db:
            print("Error: --news-db required for analyze mode")
            sys.exit(1)
        
        # Analyze recent news
        kb = KnowledgeBase()
        analyzer = NewsImpactAnalyzer(kb, tea_baselines)
        
        results = analyzer.analyze_recent_articles(
            news_db_path=args.news_db,
            days_back=args.days
        )
        
        # Save results
        if args.output:
            with open(args.output, 'w') as f:
                # Convert to JSON-serializable format
                output = {
                    'analysis_date': datetime.now().isoformat(),
                    'days_analyzed': args.days,
                    'total_articles': results['total_articles'],
                    'by_region': {
                        region: {
                            **data,
                            'impacts': []  # Remove detailed impacts (too large)
                        }
                        for region, data in results['by_region'].items()
                    }
                }
                json.dump(output, f, indent=2)
            
            print(f"\n✅ Results saved to: {args.output}")

if __name__ == "__main__":
    main()
