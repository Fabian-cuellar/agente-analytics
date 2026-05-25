"""
RAG App — Asistente de Conocimiento Interno
Arquitectura: Hybrid Search (Semantic + BM25) + ChromaDB Persistente + Streaming

Para nuevo cliente: duplicar config_nexus.yaml con los datos del cliente,
luego correr: streamlit run rag_app.py -- --config config_nuevo_cliente.yaml
"""
import streamlit as st
import anthropic
import os
import re
import logging
import hashlib
import numpy as np
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account
import chromadb
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi
import yaml

load_dotenv()

# ============================================================
# CONFIG — lee el YAML del cliente
# ============================================================
CONFIG_FILE = os.getenv("RAG_CONFIG", "config_nexus.yaml")

@st.cache_resource
def cargar_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)

cfg = cargar_config()

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
audit_logger = logging.getLogger("rag_audit")
if not audit_logger.handlers:
    audit_logger.setLevel(logging.INFO)
    _fh = logging.FileHandler("rag_log.txt", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s\n%(message)s\n" + "-" * 80))
    audit_logger.addHandler(_fh)

# ============================================================
# GOOGLE DRIVE
# ============================================================
@st.cache_resource
def get_drive_service():
    creds_file = cfg["drive"]["credentials_file"]
    if os.path.exists(creds_file):
        creds = service_account.Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    else:
        import json
        creds_info = dict(st.secrets["gcp_service_account"])
        creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
    return build("drive", "v3", credentials=creds)

@st.cache_data(ttl=300)
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

@st.cache_data(ttl=300)
def cargar_base_conocimiento():
    """Carga texto completo de todos los documentos. Sin truncado."""
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
            logging.warning(f"No se pudo leer {archivo['name']}: {e}")
    return documentos

# ============================================================
# CHUNKING SEMÁNTICO
# ============================================================
def chunkear_semantico(doc):
    """
    Chunking que respeta estructura del texto:
    1. Divide por párrafos (\n\n) — respeta unidades de sentido
    2. Fusiona párrafos cortos con el siguiente (evita micro-chunks)
    3. Divide párrafos muy largos por oraciones (evita exceder max_chars)
    """
    max_chars = cfg["rag"]["max_chunk_chars"]
    min_chars = cfg["rag"]["min_chunk_chars"]
    texto = doc["contenido"]

    # Paso 1: separar por párrafos
    paragrafos = [p.strip() for p in re.split(r'\n{2,}', texto) if p.strip()]

    # Paso 2: fusionar cortos / dividir muy largos
    chunks_texto = []
    buffer = ""
    for p in paragrafos:
        if len(p) > max_chars * 1.5:
            # Guardar buffer pendiente
            if buffer:
                chunks_texto.append(buffer)
                buffer = ""
            # Dividir por oración
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

    # Paso 3: convertir a formato estándar, descartar micro-chunks
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
# ÍNDICES: ChromaDB Persistente + BM25
# ============================================================
def construir_indices(documentos):
    """
    Construye dos índices sobre los mismos chunks:
    - ChromaDB (semántico, embeddings con sentence-transformers)
    - BM25Okapi (keyword search exacto)
    El índice vectorial se persiste en disco (chroma_path).
    """
    chroma_path = cfg["rag"]["chroma_path"]
    os.makedirs(chroma_path, exist_ok=True)

    # Generar todos los chunks semánticos
    all_chunks = []
    for doc in documentos:
        all_chunks.extend(chunkear_semantico(doc))

    # --- ChromaDB Persistente ---
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    chroma_client = chromadb.PersistentClient(path=chroma_path)

    # Eliminar colección anterior si existe (rebuild limpio)
    try:
        chroma_client.delete_collection("nexus-kb")
    except Exception:
        pass

    collection = chroma_client.create_collection(
        name="nexus-kb",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )

    # Inserción en batches
    batch_size = 50
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i:i + batch_size]
        collection.add(
            documents=[c["text"] for c in batch],
            metadatas=[{"nombre": c["nombre"], "url": c["url"]} for c in batch],
            ids=[c["id"] for c in batch]
        )

    # --- BM25 Index ---
    if not all_chunks:
        raise ValueError("No se generaron chunks. Verifica que los documentos tengan texto extraíble.")
    tokenized = [c["text"].lower().split() for c in all_chunks]
    bm25 = BM25Okapi(tokenized)

    # Guardar hash para detección de cambios
    with open(os.path.join(chroma_path, "docs_hash.txt"), "w") as f:
        f.write(calcular_hash_docs(documentos))

    logging.info(f"Índices construidos: {len(all_chunks)} chunks de {len(documentos)} documentos")
    return collection, bm25, all_chunks

def get_o_cargar_indices(documentos):
    """
    Reutiliza índices si los documentos no cambiaron (hash igual).
    Reconstruye automáticamente si detecta cambios.
    """
    current_hash = calcular_hash_docs(documentos)
    chroma_path = cfg["rag"]["chroma_path"]

    # Verificar si el índice en disco es válido
    hash_file = os.path.join(chroma_path, "docs_hash.txt")
    disk_hash = None
    if os.path.exists(hash_file):
        with open(hash_file) as f:
            disk_hash = f.read().strip()

    if (
        "vector_store" not in st.session_state
        or st.session_state.get("docs_hash") != current_hash
    ):
        if disk_hash == current_hash:
            # Índice en disco es válido — cargar sin reconstruir
            with st.spinner("⚡ Cargando índice desde disco..."):
                ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )
                chroma_client = chromadb.PersistentClient(path=chroma_path)
                collection = chroma_client.get_collection("nexus-kb", embedding_function=ef)

                # Reconstruir BM25 en memoria (no es persistible directamente)
                all_chunks = []
                for doc in documentos:
                    all_chunks.extend(chunkear_semantico(doc))
                tokenized = [c["text"].lower().split() for c in all_chunks]
                bm25 = BM25Okapi(tokenized)
        else:
            # Reconstruir desde cero
            with st.spinner("🔍 Indexando documentos (primera vez ~20s)..."):
                collection, bm25, all_chunks = construir_indices(documentos)

        st.session_state.vector_store = collection
        st.session_state.bm25_index = bm25
        st.session_state.all_chunks = all_chunks
        st.session_state.docs_hash = current_hash
        st.session_state.n_chunks = len(all_chunks)

    return (
        st.session_state.vector_store,
        st.session_state.bm25_index,
        st.session_state.all_chunks
    )

# ============================================================
# HYBRID SEARCH — Reciprocal Rank Fusion (RRF)
# ============================================================

def buscar_hibrido(query, collection, bm25, all_chunks):
    """
    Two-stage retrieval: todos los documentos compiten, los mejores aportan contexto.

    Stage 1 — Scoring global (RRF sobre todos los chunks):
        - Semántica (ChromaDB): encuentra sinónimos y conceptos relacionados
        - BM25: garantiza que nombres propios y términos exactos no se pierdan
        - RRF combina ambos rankings sin necesitar pesos manuales

    Stage 2 — Selección por documento:
        - Cada documento obtiene un "score representativo" (su mejor chunk)
        - Se rankean los documentos por ese score → todos compiten
        - De los top-N documentos se extraen sus mejores chunks
        - Resultado: cobertura garantizada de los docs más relevantes

    Escala a cientos de documentos sin hardcode.
    """
    if not all_chunks:
        return [], [], {}

    top_k       = cfg["rag"]["top_k"]           # chunks totales enviados a Claude
    max_por_doc = cfg["rag"].get("max_chunks_per_doc", 2)  # chunks por documento ganador
    k_rrf       = 60
    chunk_map   = {c["id"]: c for c in all_chunks}
    n_total     = len(all_chunks)

    # ── Stage 1: RRF sobre todos los chunks ──────────────────────────────────

    # Semántica
    semantic    = collection.query(query_texts=[query], n_results=n_total)
    semantic_ids = semantic["ids"][0]

    # BM25
    scores_bm25 = bm25.get_scores(query.lower().split())
    bm25_ranked = [all_chunks[i]["id"] for i in np.argsort(scores_bm25)[::-1]]

    # RRF
    rrf_scores = {}
    for rank, cid in enumerate(semantic_ids):
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k_rrf + rank + 1)
    for rank, cid in enumerate(bm25_ranked):
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1 / (k_rrf + rank + 1)

    # ── Stage 2: Ranking por documento ───────────────────────────────────────

    # Score representativo de cada documento = score de su mejor chunk
    doc_best_score = {}   # doc_nombre → mejor rrf_score
    doc_chunks_ranked = {}  # doc_nombre → lista de (cid, score) ordenada desc

    for cid, score in rrf_scores.items():
        if cid not in chunk_map:
            continue
        nombre = chunk_map[cid]["nombre"]
        if nombre not in doc_best_score or score > doc_best_score[nombre]:
            doc_best_score[nombre] = score
        if nombre not in doc_chunks_ranked:
            doc_chunks_ranked[nombre] = []
        doc_chunks_ranked[nombre].append((cid, score))

    # Ordenar chunks dentro de cada doc
    for nombre in doc_chunks_ranked:
        doc_chunks_ranked[nombre].sort(key=lambda x: x[1], reverse=True)

    # Rankear documentos por su score representativo
    docs_ordenados = sorted(doc_best_score, key=lambda n: doc_best_score[n], reverse=True)

    # Calcular cuántos docs necesitamos para llegar a top_k chunks
    n_docs_necesarios = -(-top_k // max_por_doc)  # ceil division
    docs_seleccionados = docs_ordenados[:n_docs_necesarios]

    # Extraer hasta max_por_doc chunks de cada doc ganador
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

# ============================================================
# SCOPE VALIDATION
# ============================================================
def es_pregunta_fuera_de_scope(pregunta):
    p = pregunta.lower()
    if any(w in p for w in cfg["scope"]["palabras_validas"]):
        return False
    return any(w in p for w in cfg["scope"]["palabras_invalidas"])

# ============================================================
# AUTH
# ============================================================
def check_auth():
    if not cfg["auth"]["enabled"]:
        return True
    if st.session_state.get("authenticated"):
        return True

    st.markdown(f"## {cfg['ui']['titulo']}")
    st.markdown("Aplicación de uso interno. Ingresa tu contraseña para continuar.")
    with st.form("login_form"):
        pwd = st.text_input("Contraseña", type="password", placeholder="••••••••")
        if st.form_submit_button("Ingresar", use_container_width=True):
            key = cfg["auth"]["password_env"]
            try:
                expected = st.secrets[key]
            except Exception:
                expected = os.getenv(key, "")
            if pwd.strip() == expected.strip():
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta")
    return False

# ============================================================
# CLAUDE CLIENT
# ============================================================
api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        st.error("❌ Falta el secret ANTHROPIC_API_KEY. Ve a Settings → Secrets de esta app y agrégalo.")
        st.stop()
claude_client = anthropic.Anthropic(api_key=api_key)

color = cfg["ui"].get("color_primario", "#4F46E5")
logo_url = cfg["ui"].get("logo_url", "")

# ============================================================
# APP
# ============================================================
st.set_page_config(
    page_title=cfg["ui"]["titulo"],
    page_icon=cfg["cliente"]["icono"],
    layout="wide"
)

if logo_url:
    st.logo(logo_url)

st.markdown(f"""
<style>
    .stButton>button {{
        background-color: {color};
        color: white;
        border: none;
        border-radius: 6px;
    }}
    .stButton>button:hover {{ opacity: 0.85; }}
    section[data-testid="stSidebar"] {{
        border-right: 3px solid {color};
    }}
    .stChatMessage [data-testid="stMarkdownContainer"] a {{
        color: {color};
    }}
</style>
""", unsafe_allow_html=True)

if not check_auth():
    st.stop()

st.title(cfg["ui"]["titulo"])
st.caption(cfg["ui"]["descripcion"])

# Session state inicial
for key, default in [
    ("messages", []),
    ("chat_history", []),
    ("pregunta_count", 0),
    ("pending_question", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown(f"**¿Qué puedo preguntarle?**  \n{cfg['ui']['temas']}")
    st.divider()

    with st.spinner("Cargando documentos desde Drive..."):
        documentos = cargar_base_conocimiento()

    st.subheader("📚 Documentos disponibles")
    busqueda = st.text_input(
        "Buscar", placeholder="Filtrar por nombre...", label_visibility="collapsed"
    )
    docs_filtrados = (
        [d for d in documentos if busqueda.lower() in d["nombre"].lower()]
        if busqueda else documentos
    )
    if docs_filtrados:
        for doc in docs_filtrados:
            st.markdown(f"• [{doc['nombre']}]({doc['url']}) `{doc['modificado'][:10]}`")
    else:
        st.caption("Sin resultados para esa búsqueda.")

    st.divider()
    st.subheader("📊 Estadísticas")
    total_chars = sum(len(d["contenido"]) for d in documentos)
    _metric_preguntas = st.empty()
    _metric_preguntas.metric("Preguntas en sesión", st.session_state.pregunta_count)
    st.metric("Documentos cargados", len(documentos))
    st.metric("Chunks indexados", st.session_state.get("n_chunks", "—"))
    st.metric("Texto total", f"{total_chars // 1024} KB")

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Actualizar", use_container_width=True,
                     help="Recarga docs desde Drive y reconstruye el índice"):
            st.cache_data.clear()
            for k in ["vector_store", "bm25_index", "all_chunks", "docs_hash"]:
                st.session_state.pop(k, None)
            st.rerun()
    with col2:
        if st.button("🗑️ Limpiar", use_container_width=True,
                     help="Borra el historial de conversación"):
            st.session_state.messages = []
            st.session_state.chat_history = []
            st.session_state.pregunta_count = 0
            st.rerun()

    st.caption(
        "💡 Para agregar documentos, súbelos a la carpeta "
        "**knowledge-base-comercial** en Google Drive y haz clic en Actualizar."
    )
    st.divider()
    c = cfg["contacto"]
    st.markdown(
        f"<div style='font-size:0.78em; color: gray;'>"
        f"Desarrollado por <b>{c['nombre']}</b><br>"
        f"{c['titulo']}<br><br>"
        f"¿Quieres un asistente así para tu empresa?<br>"
        f"<a href='mailto:{c['email']}'>{c['email']}</a>"
        f"</div>",
        unsafe_allow_html=True
    )

# ============================================================
# CONSTRUIR / CARGAR ÍNDICES
# ============================================================
collection, bm25_index, all_chunks = get_o_cargar_indices(documentos)

# ============================================================
# HISTORIAL DE CHAT
# ============================================================
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("fuentes"):
            st.caption("📎 Fuentes consultadas:")
            cols = st.columns(min(len(msg["fuentes"]), 3))
            for i, f in enumerate(msg["fuentes"]):
                with cols[i % 3]:
                    st.markdown(f"[{f['nombre']}]({f['url']})")

# ============================================================
# CHAT INPUT
# ============================================================
placeholder_input = cfg["ui"]["preguntas_ejemplo"][0]
pregunta_input = st.chat_input(placeholder_input)

if st.session_state.pending_question:
    pregunta = st.session_state.pending_question
    st.session_state.pending_question = None
elif pregunta_input:
    pregunta = pregunta_input
else:
    pregunta = None

if pregunta:

    if pregunta.strip().lower() in ["salir", "limpiar"]:
        st.session_state.messages = []
        st.session_state.chat_history = []
        st.session_state.pregunta_count = 0
        st.rerun()

    st.session_state.chat_history.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)

    # --- Scope check ---
    if es_pregunta_fuera_de_scope(pregunta):
        respuesta = (
            "Solo puedo responder preguntas relacionadas con "
            f"{cfg['cliente']['nombre']}: productos, precios, procesos y políticas internas. "
            "¿En qué puedo ayudarte?"
        )
        with st.chat_message("assistant"):
            st.markdown(respuesta)
        st.session_state.chat_history.append({
            "role": "assistant", "content": respuesta, "fuentes": []
        })

    else:
        # --- Hybrid Search ---
        chunks_relevantes, fuentes_unicas, rrf_scores = buscar_hibrido(
            pregunta, collection, bm25_index, all_chunks
        )
        st.session_state["last_chunks"] = chunks_relevantes
        st.session_state["last_rrf_scores"] = rrf_scores
        contexto = construir_contexto_rag(chunks_relevantes)

        st.session_state.messages.append({
            "role": "user",
            "content": f"Fragmentos relevantes de la documentación:\n{contexto}\n\nPregunta: {pregunta}"
        })

        # --- Streaming response ---
        with st.chat_message("assistant"):
            respuesta_container = st.empty()
            respuesta = ""

            try:
                with claude_client.messages.stream(
                    model=cfg["rag"]["modelo"],
                    max_tokens=4096,
                    system=cfg["system_prompt"],
                    messages=st.session_state.messages
                ) as stream:
                    for text in stream.text_stream:
                        respuesta += text
                        respuesta_container.markdown(respuesta + "▌")
            except anthropic.APIStatusError as e:
                is_overloaded = "overloaded" in str(e.message).lower()
                if is_overloaded:
                    respuesta_container.warning(
                        "⏳ La API de Anthropic está saturada en este momento. "
                        "Espera unos segundos y vuelve a enviar tu pregunta."
                    )
                else:
                    respuesta_container.error(f"❌ Error de API ({e.status_code}): {e.message}")
                logging.error(f"APIStatusError: status={e.status_code} message={e.message}")
                # Revertir el mensaje del usuario para que el reintento funcione limpio
                if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
                    st.session_state.messages.pop()
                st.stop()
            except anthropic.APIConnectionError as e:
                respuesta_container.error(f"❌ Sin conexión con la API de Anthropic: {e}")
                logging.error(f"APIConnectionError: {e}")
                if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
                    st.session_state.messages.pop()
                st.stop()
            except Exception as e:
                respuesta_container.error(f"❌ Error inesperado: {type(e).__name__}: {e}")
                logging.error(f"Unexpected error in streaming: {type(e).__name__}: {e}")
                if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
                    st.session_state.messages.pop()
                st.stop()

            respuesta_container.markdown(respuesta)

            # Fuentes al pie
            if fuentes_unicas:
                st.caption("📎 Fuentes consultadas:")
                cols = st.columns(min(len(fuentes_unicas), 3))
                for i, f in enumerate(fuentes_unicas):
                    with cols[i % 3]:
                        st.markdown(f"[{f['nombre']}]({f['url']})")

        # --- Guardar estado ---
        st.session_state.messages.append({"role": "assistant", "content": respuesta})
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": respuesta,
            "fuentes": fuentes_unicas
        })

        # --- Audit log ---
        fuentes_log = ", ".join(f["nombre"] for f in fuentes_unicas)
        audit_logger.info(
            f"PREGUNTA:\n{pregunta}\n\n"
            f"CHUNKS: {len(chunks_relevantes)} chunks de [{fuentes_log}]\n\n"
            f"RESPUESTA:\n{respuesta}"
        )

        # --- Trim historial ---
        max_msgs = cfg["rag"]["max_history_pares"] * 2
        if len(st.session_state.messages) > max_msgs:
            st.session_state.messages = st.session_state.messages[-max_msgs:]

    st.session_state.pregunta_count += 1
    _metric_preguntas.metric("Preguntas en sesión", st.session_state.pregunta_count)

# ============================================================
# DEBUG PANEL — muestra qué chunks se recuperaron en la última pregunta
# ============================================================
if st.session_state.get("last_chunks"):
    with st.expander("🔬 Debug: chunks recuperados en la última respuesta"):
        st.caption(
            "Esto muestra exactamente qué fragmentos vio Claude para responder. "
            "Si la respuesta fue incorrecta, aquí puedes ver por qué."
        )
        for i, chunk in enumerate(st.session_state["last_chunks"]):
            score = st.session_state.get("last_rrf_scores", {}).get(chunk["id"], 0)
            st.markdown(
                f"**Chunk {i+1}** — `{chunk['nombre']}` "
                f"(RRF score: `{score:.4f}`)"
            )
            st.code(chunk["text"][:400] + ("..." if len(chunk["text"]) > 400 else ""))

# ============================================================
# EJEMPLOS
# ============================================================
with st.expander("💡 Preguntas de ejemplo"):
    for q in cfg["ui"]["preguntas_ejemplo"]:
        if st.button(q, use_container_width=True):
            st.session_state.pending_question = q
            st.rerun()
