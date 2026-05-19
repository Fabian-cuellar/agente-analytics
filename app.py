import streamlit as st
import anthropic
import io
import json
from google.cloud import bigquery
from google.oauth2 import service_account
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# --- Configuración BigQuery ---
gcp_secret = st.secrets["gcp_service_account"]
credentials_info = json.loads(gcp_secret) if isinstance(gcp_secret, str) else dict(gcp_secret)
credentials_info["private_key"] = credentials_info["private_key"].replace("\\n", "\n")
credentials = service_account.Credentials.from_service_account_info(
    credentials_info,
    scopes=["https://www.googleapis.com/auth/bigquery"]
)
bq = bigquery.Client(credentials=credentials, project=credentials_info["project_id"])

PROJECT = "bigquery-public-data"
DATASET = "thelook_ecommerce"

client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

tools = [
    {
        "name": "obtener_esquema",
        "description": "Obtiene las columnas y tipos de una tabla de BigQuery",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabla": {"type": "string", "description": "Opciones: orders, order_items, users, products, inventory_items"}
            },
            "required": ["tabla"]
        }
    },
    {
        "name": "ejecutar_sql",
        "description": "Ejecuta SQL en BigQuery y retorna resultados",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Query SQL válido para BigQuery"}
            },
            "required": ["sql"]
        }
    }
]

def obtener_esquema(tabla):
    try:
        ref = bq.get_table(f"{PROJECT}.{DATASET}.{tabla}")
        return str([(f.name, f.field_type) for f in ref.schema])
    except Exception as e:
        return f"Error: {e}"

def ejecutar_sql(sql):
    # Validación de seguridad: bloquear operaciones destructivas
    if any(k in sql.upper() for k in ["DELETE", "DROP", "UPDATE", "INSERT", "TRUNCATE"]):
        return "ERROR: Operación no permitida (DELETE/DROP/UPDATE/INSERT/TRUNCATE bloqueada por seguridad)", None
    try:
        logging.info(f"SQL: {sql}")
        resultado = bq.query(sql).to_dataframe()
        if resultado.empty:
            return "Sin resultados", None
        return resultado.to_string(index=False, max_rows=20), resultado
    except Exception as e:
        logging.error(f"Error SQL: {e}")
        return f"ERROR_SQL: {str(e)}", None

SYSTEM = f"""Eres un analista de datos experto conectado a BigQuery.
Dataset: {PROJECT}.{DATASET}
Tablas: orders, order_items, users, products, inventory_items

Flujo:
1. Usa obtener_esquema si no conoces las columnas
2. Genera SQL y usa ejecutar_sql
3. Si recibes ERROR_SQL, corrige y reintenta
4. Responde en español con números formateados

Reglas SQL BigQuery:
- Siempre: `{PROJECT}.{DATASET}.nombre_tabla`
- Fechas: DATE(created_at)
- Sin LIMIT en agregaciones
- Usa LEFT JOIN para mantener todos los registros de la tabla principal aunque no haya coincidencia en la secundaria"""

# --- UI Streamlit ---
st.set_page_config(page_title="QueryAI", page_icon="⚡", layout="wide")

st.title("⚡ QueryAI")
st.markdown("Escribe una pregunta en español, la IA genera el SQL, consulta la base de datos en tiempo real y te responde en lenguaje natural.")

# Contexto del dataset prominente en la parte superior
st.info(
    "🔗 **Conectado a BigQuery** · Dataset: `thelook_ecommerce` · "
    "Dataset de ecommerce con ventas, productos y clientes reales · Consultas en tiempo real"
)

# --- Session state ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None

# --- Sidebar ---
with st.sidebar:
    st.markdown("### 💡 Preguntas de ejemplo")
    st.markdown("Haz clic para ejecutar directamente:")

    example_questions = [
        "¿Cuál fue el mes con más ingresos?",
        "¿Cuánto vendimos el último trimestre?",
        "¿Qué categoría tiene mayor margen?",
        "¿Cuáles son los 5 productos más vendidos?",
        "¿De qué país tenemos más usuarios?",
        "¿Cuál es la edad promedio de nuestros compradores?",
    ]

    for q in example_questions:
        if st.button(q, use_container_width=True):
            st.session_state.pending_question = q
            st.rerun()

    st.divider()

    with st.expander("⚙️ Cómo funciona"):
        st.markdown("""
**1. Escribes tu pregunta**
En lenguaje natural, sin saber SQL.

**2. Claude genera SQL automáticamente**
Analiza las tablas disponibles y construye la consulta correcta.

**3. Obtienes respuesta con datos reales**
Los resultados vienen directamente de BigQuery en tiempo real.
""")

    st.divider()
    st.markdown(
        "Creado por **Fabián Cuellar** · AI Automation Specialist  \n"
        "✉ fabian.cuellar.retamal@gmail.com"
    )

st.divider()

# Botón limpiar chat
if st.button("🗑️ Limpiar chat"):
    st.session_state.messages = []
    st.session_state.chat_history = []
    st.rerun()

# Mostrar historial de chat
for i, msg in enumerate(st.session_state.chat_history):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "sql" in msg:
            with st.expander("Ver SQL ejecutado"):
                st.code(msg["sql"], language="sql")
        if "dataframe" in msg:
            df_display = msg["dataframe"].copy()
            df_display.index = range(1, len(df_display) + 1)
            st.dataframe(df_display)
            buf = io.BytesIO()
            msg["dataframe"].to_excel(buf, index=False)
            st.download_button("⬇️ Descargar Excel", buf.getvalue(), "resultados.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"dl_hist_{i}")

# Input del usuario — acepta tanto texto escrito como pregunta de botón
typed_input = st.chat_input("¿Cuáles son los productos más vendidos?")

if st.session_state.pending_question:
    pregunta = st.session_state.pending_question
    st.session_state.pending_question = None
elif typed_input:
    pregunta = typed_input
else:
    pregunta = None

if pregunta:
    logging.info(f"Pregunta: {pregunta}")

    st.session_state.chat_history.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)

    st.session_state.messages.append({"role": "user", "content": pregunta})

    with st.chat_message("assistant"):
        with st.spinner("Analizando..."):
            df_resultado = None
            last_sql = None

            while True:
                response = client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=2048,
                    system=SYSTEM,
                    tools=tools,
                    messages=st.session_state.messages
                )

                if response.stop_reason == "tool_use":
                    tool_blocks = [b for b in response.content if b.type == "tool_use"]
                    tool_results = []

                    for tb in tool_blocks:
                        if tb.name == "ejecutar_sql":
                            last_sql = tb.input["sql"]
                            texto, df = ejecutar_sql(last_sql)
                            if df is not None:
                                df_resultado = df
                        else:
                            texto = obtener_esquema(tb.input["tabla"])

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": texto
                        })

                    st.session_state.messages.append({"role": "assistant", "content": response.content})
                    st.session_state.messages.append({"role": "user", "content": tool_results})

                else:
                    respuesta = response.content[0].text
                    st.markdown(respuesta)

                    if last_sql:
                        with st.expander("Ver SQL ejecutado"):
                            st.code(last_sql, language="sql")

                    if df_resultado is not None:
                        df_display = df_resultado.copy()
                        df_display.index = range(1, len(df_display) + 1)
                        st.dataframe(df_display)
                        buf = io.BytesIO()
                        df_resultado.to_excel(buf, index=False)
                        st.download_button("⬇️ Descargar Excel", buf.getvalue(), "resultados.xlsx",
                                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                           key="dl_new")

                    msg = {"role": "assistant", "content": respuesta}
                    if df_resultado is not None:
                        msg["dataframe"] = df_resultado
                    if last_sql:
                        msg["sql"] = last_sql
                    st.session_state.chat_history.append(msg)
                    st.session_state.messages.append({"role": "assistant", "content": respuesta})
                    logging.info(f"Respuesta: {respuesta[:200]}")
                    break
