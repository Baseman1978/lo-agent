"""Documenten het brein in — verslagen, whitepapers, boeken.

Een document wordt: één Document-node + één samenvattings-fragment +
inhouds-chunks als MemoryFragments met embeddings. Daarmee is alles
direct vindbaar via brain_search en zichtbaar in het hologram.

Grote documenten worden begrensd (MAX_CHUNKS) zodat een boek het brein
niet overspoelt — de samenvatting dekt dan het geheel.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any

from span.config import PROJECT_ROOT

CHUNK_SIZE = 1400
CHUNK_OVERLAP = 150
MAX_CHUNKS = 100
MAX_BYTES = 25_000_000

# Gestructureerde opslag: origineel + markdown naast elkaar, per document-id.
# In Docker gemount op ./documents zodat Bas er in de verkenner bij kan.
DOCS_DIR = Path(os.environ.get("SPAN_DOCS_DIR", str(PROJECT_ROOT / "documents")))

MARKITDOWN_TYPES = (".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".html", ".htm",
                    ".eml", ".csv", ".json", ".xml")
TEXT_TYPES = (".txt", ".md")

META_PROMPT = """Je bent het document-subsysteem van Span. Hieronder het begin van een
document dat Bas aan zijn geheugen toevoegt. Lever:
1. summary: Nederlandse samenvatting van 3-6 zinnen — waar gaat het over,
   kernpunten, waarom bewaren.
2. entities: concrete eigennamen erin (personen, projecten, bedrijven), max 6.

Antwoord met uitsluitend JSON:
{"summary": "<samenvatting>", "entities": [{"name": "<eigennaam>", "etype": "person|project|company"}]}"""


def extract_text(filename: str, raw: bytes) -> str:
    """Document → Markdown/tekst. MarkItDown eerst (structuurbehoud, veel
    formaten); valt terug op de kale extractie als die niet beschikbaar is."""
    name = filename.lower()
    if name.endswith(TEXT_TYPES):
        return raw.decode("utf-8", errors="replace")
    if not name.endswith(MARKITDOWN_TYPES):
        raise ValueError(f"Bestandstype niet ondersteund: {filename} "
                         "(wel: pdf, docx, pptx, xlsx, html, eml, csv, json, xml, txt, md)")
    try:
        from markitdown import MarkItDown
        result = MarkItDown(enable_plugins=False).convert_stream(
            io.BytesIO(raw), file_extension=os.path.splitext(name)[1]
        )
        text = (result.text_content or "").strip()
        if text:
            return text
    except ImportError:
        pass  # markitdown niet geïnstalleerd (bv. lokale dev) → legacy pad
    except Exception:
        pass  # conversie-fout → legacy pad proberen
    return _extract_legacy(name, raw)


def _extract_legacy(name: str, raw: bytes) -> str:
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if name.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs)
    if name.endswith((".csv", ".json", ".xml", ".html", ".htm", ".eml")):
        return raw.decode("utf-8", errors="replace")
    raise ValueError(f"Kon geen tekst halen uit {name}.")


def _store_files(doc_id: str, filename: str, raw: bytes, markdown: str) -> str:
    """Origineel + markdown-versie op schijf; pad komt op de Document-node."""
    safe = re.sub(r"[^\w.\- ]", "_", os.path.basename(filename)).strip() or "document"
    folder = DOCS_DIR / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / safe).write_bytes(raw)
    (folder / (os.path.splitext(safe)[0] + ".md")).write_text(markdown, encoding="utf-8")
    return str(folder)


def chunk_text(text: str) -> list[str]:
    """Overlappende chunks op alinea-vriendelijke grenzen."""
    text = " ".join(text.split())  # whitespace normaliseren
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text) and len(chunks) < MAX_CHUNKS:
        end = start + CHUNK_SIZE
        if end < len(text):
            cut = text.rfind(". ", start + CHUNK_SIZE // 2, end)
            if cut > 0:
                end = cut + 1
        chunks.append(text[start:end].strip())
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [c for c in chunks if len(c) > 40]


def ingest_document(state: dict[str, Any], filename: str, raw: bytes,
                    scope: str = "algemeen") -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise ValueError("Bestand te groot (max 25 MB).")
    text = extract_text(filename, raw)
    if len(text.strip()) < 50:
        raise ValueError("Geen leesbare tekst gevonden in het document.")
    chunks = chunk_text(text)
    truncated = len(text) > MAX_CHUNKS * (CHUNK_SIZE - CHUNK_OVERLAP)

    brain = state["brain"]
    llm = state["llm"]
    from uuid import uuid4
    doc_id = f"doc-{uuid4().hex[:12]}"

    # opslag op schijf: origineel + markdown (zacht falend)
    try:
        stored_path = _store_files(doc_id, filename, raw, text)
    except Exception:
        stored_path = ""

    # samenvatting + entiteiten met het lichte model (zacht falend).
    # De documenttekst is untrusted -> spotlighten zodat het lichte model 'm
    # als DATA leest, niet als instructie (I3: injectie in geüpload bestand).
    from span.safety.scan import spotlight
    summary, entities = "", []
    try:
        parsed = llm.chat_json(
            [
                {"role": "system", "content": META_PROMPT},
                {"role": "user", "content": f"Bestand: {filename}\n\n"
                 + spotlight(text[:6000], "DOCUMENT-INHOUD")},
            ],
            model=state["settings"].model_light,
        )
        summary = (parsed.get("summary") or "").strip()
        entities = parsed.get("entities") or []
    except Exception:
        pass

    brain.run(
        """
        CREATE (:Document {
          id: $id, title: $title, chars: $chars, chunks: $chunks,
          truncated: $truncated, summary: $summary, path: $path, added: datetime()
        })
        """,
        id=doc_id, title=filename, chars=len(text), chunks=len(chunks),
        truncated=truncated, summary=summary[:1000], path=stored_path,
    )

    # entiteit-relaties: het document kent de mensen/projecten die erin staan
    for ent in entities[:6]:
        name = (ent.get("name") or "").strip()
        etype = ent.get("etype", "project")
        if len(name) < 2 or etype not in {"person", "project", "company"}:
            continue
        try:
            brain.run(
                """
                MERGE (e:Entity {name: $name})
                ON CREATE SET e.etype = $etype, e.created = datetime()
                SET e.last_seen = datetime()
                WITH e MATCH (d:Document {id: $doc})
                MERGE (d)-[:MENTIONS]->(e)
                """,
                name=name, etype=etype, doc=doc_id,
            )
        except Exception:
            pass

    from span.memory.bootstrap import start_session
    from span.memory.fragments import FragmentStore
    fragments = FragmentStore(brain, llm)
    session_id = state.get("doc_session") or start_session(brain)
    state["doc_session"] = session_id

    if summary:
        # samenvatting is Spans eigen tekst (trusted), met scope-tag (M18)
        fragments.write(
            mf_type="observation",
            content=f"Document '{filename}': {summary}",
            context=f"document {doc_id}",
            session_id=session_id,
            source="document",
            scope=scope,
        )
    stored = 0
    for i, chunk in enumerate(chunks):
        try:
            # ruwe documenttekst = untrusted ingest (scan + spotlight in RAG)
            mf_id = fragments.write_external(
                mf_type="observation",
                content=chunk,
                context=f"{filename} · deel {i + 1}/{len(chunks)}",
                session_id=session_id,
                source="document",
                scope=scope,
            )["id"]
            brain.run(
                "MATCH (d:Document {id: $doc}), (mf:MemoryFragment {id: $mf}) "
                "CREATE (d)-[:HAS_CHUNK]->(mf)",
                doc=doc_id, mf=mf_id,
            )
            stored += 1
        except Exception:
            continue
    return {"id": doc_id, "title": filename, "chunks": stored,
            "chars": len(text), "truncated": truncated,
            "summary": summary[:300]}
