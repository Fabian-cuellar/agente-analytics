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

from config import ANTHROPIC_API_KEY, INVESTIGATOR_MODEL, ICP_DEFINITION, _HTTP_CLIENT
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
    """
    MOCK — En producción: Serper API o Tavily
    import requests
    r = requests.post("https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_API_KEY},
        json={"q": query, "num": 10})
    return json.dumps(r.json()["organic"][:5])
    """
    mock_data = {
        "growclub": {
            "general": """
Resultados para "growclub.io B2B":
1. GrowClub.io — Comunidad y formación en prospección B2B para equipos de ventas en LATAM
   URL: https://growclub.io | Descripción: Lucas Clavero fundó GrowClub en 2023 para democratizar
   el conocimiento de ventas B2B en Latinoamérica. Ofrecen cursos online, comunidad privada
   de SDRs/BDRs, y templates de outbound. MRR estimado: USD 15-30k.

2. LinkedIn GrowClub: 2.400 seguidores. Postean 3-4 veces por semana sobre cold email,
   LinkedIn outbound, y métricas de conversión.

3. Notion público de GrowClub: Templates de secuencias de email, scripts de llamadas frías,
   frameworks de ICP para equipos de 1-10 vendedores.
""",
            "news": """
Noticias recientes GrowClub (últimos 3 meses):
- Lanzaron "GrowClub Pro" (Marzo 2025): plan enterprise para equipos de +5 vendedores — USD 297/mes
- Lucas Clavero dio keynote en SaaS Summit Santiago (Abril 2025) sobre "outbound en era de IA"
- Colaboración anunciada con HubSpot LATAM para crear contenido conjunto
- 500 miembros activos en comunidad Discord
""",
            "tech": """
Stack tecnológico inferido GrowClub:
- CRM: HubSpot (mencionado en varios posts, partner oficial)
- Email outreach: Lemlist o Apollo.io (mencionados en su contenido)
- Comunidad: Discord + Circle.so
- Pagos: Stripe (USD) + Mercado Pago (CLP/ARS)
- Hosting/web: Webflow
- Video: Loom para tutoriales
"""
        }
    }

    # Lookup flexible
    for keyword in ["growclub", "grow"]:
        if keyword in query.lower():
            return mock_data["growclub"].get(search_type, mock_data["growclub"]["general"])

    return f"[MOCK] Resultados de búsqueda para '{query}' ({search_type}): Sin datos mock disponibles para esta empresa. En producción esto llamaría a Serper/Tavily."


def _mock_scrape_page(url: str, extract_focus: str) -> str:
    """MOCK — En producción: Firecrawl o Playwright"""
    if "growclub.io" in url:
        return """
Página scraped: growclub.io/about

GrowClub es la comunidad #1 de ventas B2B en LATAM.
Fundador: Lucas Clavero — ex Head of Sales en Rankmi (HR-Tech chilena, Serie B).
Misión: ayudar a equipos de ventas a prospectar mejor usando metodología + tecnología.

Productos:
- GrowClub Starter: USD 97/mes — acceso comunidad + templates
- GrowClub Pro: USD 297/mes — coaching grupal semanal + acceso herramientas premium
- GrowClub Teams: precio custom — para equipos de +10 vendedores

Pain points que resuelven (su propio copy):
"El 80% del tiempo de un SDR se va en buscar y copiar información. Los emails genéricos
tienen 2% de open rate. La prospección manual no escala."

Clientes actuales: startups y scaleups B2B en Chile, Argentina, México, Colombia.
"""
    return f"[MOCK] Contenido de {url}: página mock vacía. Conectar Firecrawl en producción."


def _mock_search_linkedin(person_name: str, company_name: str, extract: list) -> str:
    """MOCK — En producción: Proxycurl API o PhantomBuster"""
    if "lucas" in person_name.lower() or "clavero" in person_name.lower():
        return """
LinkedIn Profile: Lucas Clavero
Cargo actual: CEO & Founder en GrowClub.io (2 años)
Cargo anterior: Head of Growth en Rankmi (3 años) — lideró crecimiento,
  adquisición de clientes y expansión de revenue de USD 2M a USD 8M ARR.
Educación: Ingeniería Comercial, Universidad de Chile.
Conexiones: 2.847 | Seguidores: 4.200

Posts recientes (últimos 30 días):
1. "Implementé IA en mi proceso de outbound. Resultados después de 30 días: [hilo]"
   → 234 likes, 67 comentarios. Habla de usar ChatGPT para personalizar emails pero
   menciona que "los resultados son genéricos, falta contexto real de la empresa".

2. "Por qué el 90% de los SDRs usan IA mal para prospectar [thread]"
   → Critica el uso superficial de IA, dice que falta un sistema que realmente
   investigue y personalice end-to-end.

3. "Estamos buscando una solución de IA para automatizar la investigación de leads.
   ¿Algún tool recomendado?" — Publicado hace 18 días. 43 respuestas, ninguna solución
   clara. El propio Lucas comentó "todo lo que vi es demasiado genérico".

Actividad de comentarios: activo comentando en posts de HubSpot LATAM y Clay.
"""
    return f"[MOCK] LinkedIn de {person_name} en {company_name}: datos mock no disponibles."


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
