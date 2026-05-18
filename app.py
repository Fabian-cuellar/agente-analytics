import streamlit as st
import anthropic
import os
import io
from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(filename="agente_log.txt", level=logging.INFO, format="%(asctime)s - %(message)s")

# --- Configuración BigQuery ---
credentials = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/bigquery"]
)
bq = bigquery.Client(credentials=credentials, project=credentials.project_id)

PROJECT = "bigquery-public-data"
DATASET = "thelook_ecommerce"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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
st.set_page_config(page_title="Agente Analytics", page_icon="📊", layout="wide")
st.title("📊 Agente de Analytics")
st.caption("Pregúntame cualquier cosa sobre las ventas en lenguaje natural")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

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
            st.dataframe(msg["dataframe"])
            buf = io.BytesIO()
            msg["dataframe"].to_excel(buf, index=False)
            st.download_button("⬇️ Descargar Excel", buf.getvalue(), "resultados.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"dl_hist_{i}")

# Input del usuario
if pregunta := st.chat_input("¿Cuáles son los productos más vendidos?"):
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
                        st.dataframe(df_resultado)
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
