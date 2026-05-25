"""
RAG API — Asistente de Conocimiento Interno
FastAPI backend para consumir desde Next.js (Sergio) u cualquier frontend.

Endpoints:
  POST /ask/stream   — pregunta con respuesta en streaming (SSE)
  POST /ask          — pregunta con respuesta completa (JSON)
  GET  /status       — docs indexados, hash, estado del sistema
  POST /sync         — fuerza re-sincronización con Drive
  GET  /health       — health check simple

Configuración: variable de entorno RAG_CONFIG (default: config_nexus.yaml)
Autenticación: header X-API-Key (setear API_SECRET en .env)
"""

import os
import re
import gc
import json
import logging
import hashlib
import asyncio
import numpy as np
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import anthropic
import yaml
import chromadb
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi
from googleapiclient.discovery import build
from google.oauth2 import service_account

load_dotenv()

# ============================================================
# CONFIG
# ============================================================
CONFIG_FILE = os.getenv("RAG_CONFIG", "config_nexus.yaml")

with open(CONFIG_FILE, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("rag_api")

# ============================================================
# AUTH — API Key simple para que solo Sergio pueda consumir la API
# ============================================================
API_SECRET = os.getenv("API_SECRET", "")

def verificar_api_key(x_api_key: str = Header(default="")):
    if API_SECRET and x_api_key != API_SECRET:
        raise HTTPException(status_code=401, detail="API Key inválida")
    return True

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(
    title="RAG API — Asistente de Conocimiento Interno",
    description="Backend RAG con Hybrid Search para consumir desde Next.js",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # En prod: reemplazar por dominio de Sergio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# ESTADO GLOBAL (índices en memoria, se reconstruyen al iniciar)
# ============================================================
_state = {
    "collection": None,
    "bm25": None,
    "all_chunks": None,
    "docs_hash": None,
    "documentos": None,
    "n_docs": 0,
}

# ============================================================
# GOOGLE DRIVE
# ============================================================
def get_drive_service():
    creds_file = cfg["drive"]["credentials_file"]
    if os.path.exists(creds_file):
        creds = service_account.Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    else:
        creds_info = json.loads(os.getenv("GCP_SERVICE_ACCOUNT_JSON", "{}"))
        creds_info["private_key"] = creds_info.get("private_key", "").replace("\\n", "\n")
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    return build("drive", "v3", credentials=creds)

def listar_documentos():
    service = get_drive_service()
    results = service.files().list(
        q=f"'{cfg['drive']['folder_id']}' in parents and trashed=false",
        fields="files(id, name, mimeType, modifiedTime)",
        orderBy="modifiedTime desc"
    ).execute()
    return results.get("files", [])

def limpiar_texto(texto):
    texto = re.sub(r'\*+', '', texto)
    texto = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', texto)
    texto = re.sub(r' {3,}', ' ', texto)
    texto = re.sub(r'\n{4,}', '\n\n\n', texto)
    return texto.strip()

def descargar_texto(file_id, mime_type):
    import io
    service = get_drive_service()

    if mime_type == "application/pdf":
        import pdfplumber
        content = service.files().get_media(fileId=file_id).execute()
        paginas = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                partes = []
                texto = page.extract_text() or ""
                if texto.strip():
                    partes.append(texto)
                for tabla in page.extract_tables():
                    if not tabla or len(tabla) < 2:
                        continue
                    headers = [str(c).strip() if c else "" for c in tabla[0]]
                    for fila in tabla[1:]:
                        if not fila:
                            continue
                        celdas = [str(c).strip() if c else "" for c in fila]
                        fila_texto = " | ".join(
                            f"{h}: {v}" for h, v in zip(headers, celdas) if v and h
                        )
                        if fila_texto:
                            partes.append(fila_texto)
                paginas.append("\n".join(partes))
        return "\n\n".join(paginas)

    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        import docx
        content = service.files().get_media(fileId=file_id).execute()
        doc = docx.Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    elif mime_type == "text/plain":
        content = service.files().get_media(fileId=file_id).execute()
        return content.decode("utf-8", errors="replace")

    elif mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        import openpyxl
        content = service.files().get_media(fileId=file_id).execute()
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        partes = []
        for sheet in wb.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            headers = [str(c) if c is not None else "" for c in rows[0]]
            for fila in rows[1:]:
                celdas = [str(c) if c is not None else "" for c in fila]
                fila_texto = " | ".join(
                    f"{h}: {v}" for h, v in zip(headers, celdas) if v and h
                )
                if fila_texto:
                    partes.append(fila_texto)
        return "\n".join(partes)

    elif mime_type == "text/csv":
        content = service.files().get_media(fileId=file_id).execute()
        return content.decode("utf-8", errors="replace")

    elif mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        from pptx import Presentation
        content = service.files().get_media(fileId=file_id).execute()
        prs = Presentation(io.BytesIO(content))
        partes = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        texto = para.text.strip()
                        if texto:
                            partes.append(texto)
        return "\n".join(partes)

    elif mime_type == "application/vnd.google-apps.presentation":
        content = service.files().export(
            fileId=file_id,
            mimeType="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ).execute()
        from pptx import Presentation
        import io
        prs = Presentation(io.BytesIO(content))
        partes = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        texto = para.text.strip()
                        if texto:
                            partes.append(texto)
        return "\n".join(partes)

    elif "document" in mime_type:
        content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        return content.decode("utf-8")

    elif "spreadsheet" in mime_type:
        content = service.files().export(fileId=file_id, mimeType="text/csv").execute()
        return content.decode("utf-8")

    return ""

def cargar_base_conocimiento():
    archivos = listar_documentos()
    documentos = []
    for archivo in archivos:
        try:
            texto = descargar_texto(archivo["id"], archivo["mimeType"])
            if texto.strip():
                documentos.append({
                    "id": archivo["id"],
                    "nombre": archivo["name"],
                    "contenido": limpiar_texto(texto),
                    "modificado": archivo["modifiedTime"],
                    "url": f"https://drive.google.com/file/d/{archivo['id']}/view"
                })
        except Exception as e:
            logger.warning(f"No se pudo leer {archivo['name']}: {e}")
    return documentos

# ============================================================
# CHUNKING
# ============================================================
def chunkear_semantico(doc):
    max_chars = cfg["rag"]["max_chunk_chars"]
    min_chars = cfg["rag"]["min_chunk_chars"]
    texto = doc["contenido"]
    paragrafos = [p.strip() for p in re.split(r'\n{2,}', texto) if p.strip()]
    chunks_texto = []
    buffer = ""
    for p in paragrafos:
        if len(p) > max_chars * 1.5:
            if buffer:
                chunks_texto.append(buffer)
                buffer = ""
            oraciones = re.split(r'(?<=[.!?\n])\s+', p)
            sub = ""
            for o in oraciones:
                if len(sub) + len(o) + 1 <= max_chars:
                    sub += (" " if sub else "") + o
                else:
                    if sub:
                        chunks_texto.append(sub)
                    sub = o
            if sub:
                chunks_texto.append(sub)
        elif len(buffer) + len(p) + 2 <= max_chars:
            buffer += ("\n\n" if buffer else "") + p
        else:
            if buffer:
                chunks_texto.append(buffer)
            buffer = p
    if buffer:
        chunks_texto.append(buffer)
    chunks = []
    for i, text in enumerate(chunks_texto):
        if len(text) >= min_chars:
            chunks.append({
                "id": f"{doc['id']}_{i}",
                "text": text,
                "nombre": doc["nombre"],
                "url": doc["url"]
            })
    return chunks

def calcular_hash_docs(documentos):
    key = "|".join(f"{d['id']}:{d['modificado']}" for d in documentos)
    return hashlib.md5(key.encode()).hexdigest()

# ============================================================
# ÍNDICES
# ============================================================
def construir_indices(documentos):
    chroma_path = cfg["rag"]["chroma_path"]
    os.makedirs(chroma_path, exist_ok=True)
    all_chunks = []
    for doc in documentos:
        all_chunks.extend(chunkear_semantico(doc))
    # Modelo liviano: 60MB RAM vs 120MB del L6. Suficiente para docs en español.
    # Cambiar a "all-MiniLM-L6-v2" si tienes >1GB RAM disponible.
    EMBED_MODEL = os.getenv("EMBED_MODEL", "paraphrase-MiniLM-L3-v2")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )
    chroma_client = chromadb.PersistentClient(path=chroma_path)
    try:
        chroma_client.delete_collection("nexus-kb")
    except Exception:
        pass
    collection = chroma_client.create_collection(
        name="nexus-kb",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )
    batch_size = 30  # Batches más pequeños = menor pico de RAM durante construcción
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i:i + batch_size]
        collection.add(
            documents=[c["text"] for c in batch],
            metadatas=[{"nombre": c["nombre"], "url": c["url"]} for c in batch],
            ids=[c["id"] for c in batch]
        )
        gc.collect()  # Liberar memoria entre batches
    if not all_chunks:
        raise ValueError("No se generaron chunks.")
    tokenized = [c["text"].lower().split() for c in all_chunks]
    bm25 = BM25Okapi(tokenized)
    with open(os.path.join(chroma_path, "docs_hash.txt"), "w") as f:
        f.write(calcular_hash_docs(documentos))
    logger.info(f"Índices construidos: {len(all_chunks)} chunks de {len(documentos)} documentos")
    return collection, bm25, all_chunks

def inicializar_indices():
    """Se llama al arrancar la API. Carga o construye índices."""
    logger.info("Inicializando índices RAG...")
    documentos = cargar_base_conocimiento()
    current_hash = calcular_hash_docs(documentos)
    chroma_path = cfg["rag"]["chroma_path"]
    hash_file = os.path.join(chroma_path, "docs_hash.txt")
    disk_hash = None
    if os.path.exists(hash_file):
        with open(hash_file) as f:
            disk_hash = f.read().strip()
    if disk_hash == current_hash and os.path.exists(chroma_path):
        logger.info("Cargando índice desde disco...")
        EMBED_MODEL = os.getenv("EMBED_MODEL", "paraphrase-MiniLM-L3-v2")
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBED_MODEL
        )
        chroma_client = chromadb.PersistentClient(path=chroma_path)
        collection = chroma_client.get_collection("nexus-kb", embedding_function=ef)
        all_chunks = []
        for doc in documentos:
            all_chunks.extend(chunkear_semantico(doc))
        tokenized = [c["text"].lower().split() for c in all_chunks]
        bm25 = BM25Okapi(tokenized)
    else:
        logger.info("Construyendo índices desde cero...")
        collection, bm25, all_chunks = construir_indices(documentos)
    _state["collection"] = collection
    _state["bm25"] = bm25
    _state["all_chunks"] = all_chunks
    _state["docs_hash"] = current_hash
    _state["documentos"] = documentos
    _state["n_docs"] = len(documentos)
    gc.collect()  # Liberar memoria de construcción antes de servir requests
    logger.info(f"API lista: {len(all_chunks)} chunks de {len(documentos)} documentos")

# ============================================================
# HYBRID SEARCH
# ============================================================
def buscar_hibrido(query):
    collection = _state["collection"]
    bm25 = _state["bm25"]
    all_chunks = _state["all_chunks"]
    if not all_chunks:
        return [], [], {}
    top_k = cfg["rag"]["top_k"]
    max_por_doc = cfg["rag"].get("max_chunks_per_doc", 2)
    k_rrf = 60
    chunk_map = {c["id"]: c for c in all_chunks}
    n_total = len(all_chunks)
    semantic = collection.query(query_texts=[query], n_results=n_total)
    semantic_ids = semantic["ids"][0]
    scores_bm25 = bm25.get_scores(query.lower().split())
    bm25_ranked = [all_chunks[i]["id"] for i in np.argsort(scores_bm25)[::-1]]
    rrf_scores = {}
    for rank, cid in enumerate(semantic_ids):
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k_rrf + rank + 1)
    for rank, cid in enumerate(bm25_ranked):
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k_rrf + rank + 1)
    doc_best_score = {}
    doc_chunks_ranked = {}
    for cid, score in rrf_scores.items():
        if cid not in chunk_map:
            continue
        nombre = chunk_map[cid]["nombre"]
        if nombre not in doc_best_score or score > doc_best_score[nombre]:
            doc_best_score[nombre] = score
        if nombre not in doc_chunks_ranked:
            doc_chunks_ranked[nombre] = []
        doc_chunks_ranked[nombre].append((cid, score))
    for nombre in doc_chunks_ranked:
        doc_chunks_ranked[nombre].sort(key=lambda x: x[1], reverse=True)
    docs_ordenados = sorted(doc_best_score, key=lambda n: doc_best_score[n], reverse=True)
    n_docs_necesarios = -(-top_k // max_por_doc)
    docs_seleccionados = docs_ordenados[:n_docs_necesarios]
    chunks_resultado = []
    for nombre in docs_seleccionados:
        for cid, _ in doc_chunks_ranked[nombre][:max_por_doc]:
            chunks_resultado.append(chunk_map[cid])
            if len(chunks_resultado) >= top_k:
                break
        if len(chunks_resultado) >= top_k:
            break
    fuentes_vistas = {}
    for c in chunks_resultado:
        fuentes_vistas[c["nombre"]] = c["url"]
    fuentes_unicas = [{"nombre": k, "url": v} for k, v in fuentes_vistas.items()]
    return chunks_resultado, fuentes_unicas, rrf_scores

def construir_contexto_rag(chunks):
    return "\n\n".join(f"=== {c['nombre']} ===\n{c['text']}" for c in chunks)

def es_fuera_de_scope(pregunta):
    p = pregunta.lower()
    if any(w in p for w in cfg["scope"]["palabras_validas"]):
        return False
    return any(w in p for w in cfg["scope"]["palabras_invalidas"])

# ============================================================
# CLAUDE CLIENT
# ============================================================
api_key_anthropic = os.getenv("ANTHROPIC_API_KEY")
if not api_key_anthropic:
    raise RuntimeError("Falta ANTHROPIC_API_KEY en variables de entorno")
claude_client = anthropic.Anthropic(api_key=api_key_anthropic)

# ============================================================
# MODELOS DE REQUEST/RESPONSE
# ============================================================
class PreguntaRequest(BaseModel):
    pregunta: str
    historial: list = []   # [{"role": "user"|"assistant", "content": "..."}]

class RespuestaResponse(BaseModel):
    respuesta: str
    fuentes: list
    pregunta: str

# ============================================================
# STARTUP — inicializar índices al arrancar
# ============================================================
@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, inicializar_indices)

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return {"status": "ok", "docs": _state["n_docs"]}


@app.get("/status")
def status(_: bool = Depends(verificar_api_key)):
    """Retorna estado del sistema: docs indexados, hash, config."""
    return {
        "n_documentos": _state["n_docs"],
        "n_chunks": len(_state["all_chunks"]) if _state["all_chunks"] else 0,
        "docs_hash": _state["docs_hash"],
        "cliente": cfg["cliente"]["nombre"],
        "modelo": cfg["rag"]["modelo"],
        "documentos": [
            {"nombre": d["nombre"], "modificado": d["modificado"]}
            for d in (_state["documentos"] or [])
        ]
    }


@app.post("/sync")
def sync(_: bool = Depends(verificar_api_key)):
    """Fuerza re-sincronización completa con Google Drive."""
    try:
        inicializar_indices()
        return {
            "ok": True,
            "n_documentos": _state["n_docs"],
            "n_chunks": len(_state["all_chunks"])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask", response_model=RespuestaResponse)
def ask(req: PreguntaRequest, _: bool = Depends(verificar_api_key)):
    """Respuesta completa (no streaming). Útil para integraciones simples."""
    if es_fuera_de_scope(req.pregunta):
        return RespuestaResponse(
            respuesta="Esta pregunta está fuera del alcance del asistente.",
            fuentes=[],
            pregunta=req.pregunta
        )
    chunks, fuentes, _ = buscar_hibrido(req.pregunta)
    contexto = construir_contexto_rag(chunks)
    messages = list(req.historial) + [{
        "role": "user",
        "content": f"Fragmentos relevantes de la documentación:\n{contexto}\n\nPregunta: {req.pregunta}"
    }]
    try:
        response = claude_client.messages.create(
            model=cfg["rag"]["modelo"],
            max_tokens=4096,
            system=cfg["system_prompt"],
            messages=messages
        )
        respuesta = response.content[0].text
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Error API Anthropic: {e.message}")
    return RespuestaResponse(respuesta=respuesta, fuentes=fuentes, pregunta=req.pregunta)


@app.post("/ask/stream")
async def ask_stream(req: PreguntaRequest, _: bool = Depends(verificar_api_key)):
    """
    Streaming via Server-Sent Events (SSE).

    Formato de eventos:
      data: {"type": "chunk", "text": "..."}   — fragmento de texto
      data: {"type": "fuentes", "data": [...]}  — fuentes al final
      data: {"type": "done"}                    — fin del stream
      data: {"type": "error", "message": "..."}— error

    Consumir desde Next.js:
      const res = await fetch('/ask/stream', { method: 'POST', body: JSON.stringify({...}) })
      const reader = res.body.getReader()
      // parsear líneas SSE
    """
    if es_fuera_de_scope(req.pregunta):
        async def fuera_scope():
            yield f"data: {json.dumps({'type': 'chunk', 'text': 'Esta pregunta está fuera del alcance del asistente.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(fuera_scope(), media_type="text/event-stream")

    chunks, fuentes, _ = buscar_hibrido(req.pregunta)
    contexto = construir_contexto_rag(chunks)
    messages = list(req.historial) + [{
        "role": "user",
        "content": f"Fragmentos relevantes de la documentación:\n{contexto}\n\nPregunta: {req.pregunta}"
    }]

    async def generar() -> AsyncGenerator[str, None]:
        try:
            with claude_client.messages.stream(
                model=cfg["rag"]["modelo"],
                max_tokens=4096,
                system=cfg["system_prompt"],
                messages=messages
            ) as stream:
                for text in stream.text_stream:
                    yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            # Fuentes al terminar
            yield f"data: {json.dumps({'type': 'fuentes', 'data': fuentes})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except anthropic.APIStatusError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e.message)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generar(), media_type="text/event-stream")
