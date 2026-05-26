"""
Demo 4 — Agente 1: Investigador
Conceptos clave:
  - Tool Use: Claude decide qué herramientas llamar y cuándo
  - Prompt Caching: el ICP context no se retokeniza en cada request
  - Agentic loop: seguimos hasta que Claude dice "tool_use terminado"
"""
import json
import anthropic
from typing import Any

import requests as http_requests
from config import ANTHROPIC_API_KEY, INVESTIGATOR_MODEL, ICP_DEFINITION, _HTTP_CLIENT, SERPER_API_KEY
from models import ProspectRequest, CompanyResearch

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=_HTTP_CLIENT)


# ── TOOLS DEFINITION ──────────────────────────────────────────────────────────
# Estas son las herramientas que Claude puede llamar.
# En producción: search_web → Serper/Tavily, scrape_page → Playwright/Firecrawl

INVESTIGATOR_TOOLS = [
    {
        "name": "search_web",
        "description": (
            "Busca información en internet sobre una empresa o persona. "
            "Usa para encontrar noticias recientes, descripción de la empresa, "
            "funding, tecnología, y presencia online."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query de búsqueda. Sé específico, ej: 'growclub.io B2B prospecting LATAM 2024'"
                },
                "search_type": {
                    "type": "string",
                    "enum": ["general", "news", "linkedin", "tech"],
                    "description": "Tipo de búsqueda para filtrar fuentes"
                }
            },
            "required": ["query", "search_type"]
        }
    },
    {
        "name": "scrape_page",
        "description": (
            "Extrae el contenido de una URL específica. "
            "Usa para leer la web de la empresa, LinkedIn, o artículos relevantes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL completa a scrapear"
                },
                "extract_focus": {
                    "type": "string",
                    "description": "Qué info extraer: 'about', 'product', 'team', 'blog', 'pricing'"
                }
            },
            "required": ["url", "extract_focus"]
        }
    },
    {
        "name": "search_linkedin",
        "description": (
            "Busca el perfil LinkedIn de una persona y extrae su actividad reciente, "
            "cargo, empresa, y posts relevantes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person_name": {"type": "string"},
                "company_name": {"type": "string"},
                "extract": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["profile", "recent_posts", "activity", "connections"]},
                    "description": "Qué extraer del perfil"
                }
            },
            "required": ["person_name", "company_name", "extract"]
        }
    }
]


# ── MOCK TOOL EXECUTOR ────────────────────────────────────────────────────────
# En producción: reemplazar cada función por la API real.
# La interfaz (input/output) no cambia → los agentes no se modifican.

def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Ejecuta la herramienta y devuelve resultado como string (lo que Claude leerá)."""

    if tool_name == "search_web":
        return _mock_search_web(tool_input["query"], tool_input["search_type"])

    elif tool_name == "scrape_page":
        return _mock_scrape_page(tool_input["url"], tool_input["extract_focus"])

    elif tool_name == "search_linkedin":
        return _mock_search_linkedin(
            tool_input["person_name"],
            tool_input["company_name"],
            tool_input.get("extract", ["profile"])
        )

    return f"[ERROR] Tool '{tool_name}' no implementada."


def _mock_search_web(query: str, search_type: str) -> str:
    """Búsqueda web real con Serper. Fallback a mensaje descriptivo si no hay key."""
    if not SERPER_API_KEY:
        return f"[Sin SERPER_API_KEY] No se pudo buscar: '{query}'. Agrega SERPER_API_KEY en .env"

    try:
        endpoint = "https://google.serper.dev/news" if search_type == "news" else "https://google.serper.dev/search"
        r = http_requests.post(
            endpoint,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 5, "gl": "cl", "hl": "es"},
            timeout=10
        )
        data = r.json()

        results = data.get("organic", data.get("news", []))[:5]
        if not results:
            return f"Sin resultados para: {query}"

        output = f"Resultados de búsqueda para '{query}':\n\n"
        for i, item in enumerate(results, 1):
            output += f"{i}. {item.get('title', 'Sin título')}\n"
            output += f"   URL: {item.get('link', '')}\n"
            output += f"   {item.get('snippet', item.get('description', ''))}\n\n"
        return output

    except Exception as e:
        return f"Error en búsqueda Serper: {e}"


def _mock_scrape_page(url: str, extract_focus: str) -> str:
    """Scraping básico via Serper — busca info de la URL en lugar de scraping real.
    Para scraping real: Firecrawl (firecrawl.dev) — fácil de conectar después."""
    if not SERPER_API_KEY:
        return f"[Sin SERPER_API_KEY] No se pudo scrapear {url}"
    try:
        # Usamos Serper para buscar info sobre esa URL específica
        query = f"site:{url.replace('https://','').replace('http://','').split('/')[0]} {extract_focus}"
        return _mock_search_web(query, "general")
    except Exception as e:
        return f"Error scraping {url}: {e}"


def _mock_search_linkedin(person_name: str, company_name: str, extract: list) -> str:
    """Búsqueda LinkedIn via Serper (resultados públicos).
    Para datos completos: Proxycurl API — conectar en fase 2."""
    if not SERPER_API_KEY:
        return f"[Sin SERPER_API_KEY] No se pudo buscar LinkedIn de {person_name}"
    try:
        query = f"{person_name} {company_name} LinkedIn"
        return _mock_search_web(query, "general")
    except Exception as e:
        return f"Error buscando LinkedIn de {person_name}: {e}"


# ── SYSTEM PROMPT CON PROMPT CACHING ─────────────────────────────────────────
# cache_control: {"type": "ephemeral"} en el bloque largo → se cachea por 5 min
# Ahorro: el ICP_DEFINITION no se retokeniza en cada llamada del agentic loop

def build_system_messages(request: ProspectRequest) -> list[dict]:
    """
    Construye los mensajes del sistema con prompt caching activado.
    El bloque largo (ICP_DEFINITION) se marca como cacheable.
    """
    return [
        {
            "type": "text",
            "text": "Eres un investigador de inteligencia comercial B2B de élite. Tu trabajo es investigar empresas y contactos para identificar oportunidades de venta. Eres meticuloso, específico y siempre buscas señales de dolor reales."
        },
        {
            "type": "text",
            "text": ICP_DEFINITION,
            # PROMPT CACHING: este bloque largo se cachea entre llamadas del loop
            # En producción con requests repetidos el ahorro puede ser 60-80% en tokens de input
            "cache_control": {"type": "ephemeral"}
        }
    ]


# ── AGENTIC LOOP ──────────────────────────────────────────────────────────────

def run_investigator(request: ProspectRequest) -> tuple[CompanyResearch, int]:
    """
    Ejecuta el agentic loop del Investigador.
    Retorna (CompanyResearch, tokens_usados)

    El loop es:
    1. Claude analiza el request y decide qué tools llamar
    2. Nosotros ejecutamos las tools y devolvemos resultados
    3. Claude procesa los resultados y puede llamar más tools
    4. Cuando Claude responde sin tool_use → terminamos
    """
    print(f"\n🔍 [Investigador] Iniciando research de: {request.company_name} / {request.contact_name}")

    messages = [
        {
            "role": "user",
            "content": f"""Investiga esta empresa y contacto para determinar si son un buen prospecto.

EMPRESA: {request.company_name}
CONTACTO: {request.contact_name}
CARGO: {request.contact_role or 'desconocido'}
CONTEXTO ADICIONAL: {request.extra_context or 'ninguno'}

Usa las tools disponibles para investigar:
1. Busca la empresa (general + noticias recientes)
2. Busca tech stack que usan
3. Scrapeá su web principal
4. Buscá el perfil LinkedIn del contacto

Luego devuelve un JSON con este esquema exacto (sin markdown, solo JSON puro):
{{
  "company_name": "...",
  "domain": "...",
  "description": "...",
  "industry": "...",
  "company_size_estimate": "...",
  "recent_news": ["...", "..."],
  "pain_points_detected": ["...", "..."],
  "tech_stack_hints": ["...", "..."],
  "social_signals": ["...", "..."],
  "contact_name": "...",
  "contact_role": "...",
  "contact_linkedin_url": "...",
  "contact_recent_activity": "...",
  "sources_used": ["...", "..."],
  "research_confidence": "high|medium|low"
}}"""
        }
    ]

    total_tokens = 0
    iteration = 0
    max_iterations = 10  # safety limit

    while iteration < max_iterations:
        iteration += 1
        print(f"  → Loop iteración {iteration}")

        response = client.messages.create(
            model=INVESTIGATOR_MODEL,
            max_tokens=4096,
            system=build_system_messages(request),
            tools=INVESTIGATOR_TOOLS,
            messages=messages
        )

        total_tokens += response.usage.input_tokens + response.usage.output_tokens

        # Log cache hits (si existen)
        if hasattr(response.usage, "cache_read_input_tokens") and response.usage.cache_read_input_tokens:
            print(f"  💾 Cache hit: {response.usage.cache_read_input_tokens} tokens cacheados")

        # Caso 1: Claude terminó (end_turn) → parseamos el JSON de su respuesta
        if response.stop_reason == "end_turn":
            # Concatenar todos los bloques de texto (a veces Claude los divide)
            text_parts = [block.text for block in response.content if hasattr(block, "text")]
            text_content = "\n".join(text_parts).strip()

            if not text_content:
                # Debug: mostrar qué devolvió Claude exactamente
                print(f"  ⚠️  Respuesta vacía. Bloques: {[type(b).__name__ for b in response.content]}")
                raise ValueError("Investigador no devolvió contenido de texto")

            # Extraer JSON: buscar el bloque entre { } ignorando texto alrededor
            import re
            # Primero intentar code block markdown
            if "```" in text_content:
                match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_content, re.DOTALL)
                if match:
                    text_content = match.group(1)
            # Si no, extraer el JSON directamente (primer { hasta el último })
            if not text_content.startswith("{"):
                match = re.search(r"\{.*\}", text_content, re.DOTALL)
                if match:
                    text_content = match.group(0)

            research_data = json.loads(text_content.strip())
            print(f"  ✅ Research completo. Confianza: {research_data.get('research_confidence')}")
            return CompanyResearch(**research_data), total_tokens

        # Caso 2: Claude quiere usar tools
        if response.stop_reason == "tool_use":
            # Agregamos la respuesta de Claude al historial
            messages.append({"role": "assistant", "content": response.content})

            # Ejecutamos cada tool que pidió Claude
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  🔧 Tool call: {block.name}({json.dumps(block.input)[:80]}...)")
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            # Devolvemos los resultados a Claude
            messages.append({"role": "user", "content": tool_results})
            continue

        # Caso 3: stop reason inesperado
        raise ValueError(f"Stop reason inesperado: {response.stop_reason}")

    raise RuntimeError(f"Investigador alcanzó el máximo de iteraciones ({max_iterations})")
