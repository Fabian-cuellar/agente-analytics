"""
Demo 4 — Agente 2: Scorer con Extended Thinking
Concepto clave: Extended Thinking permite a Claude "pensar" internamente
antes de responder. El thinking NO cuenta como output para la respuesta,
pero sí consume tokens. Es ideal para evaluaciones complejas donde queremos
razonamiento profundo, no respuestas rápidas.

CUÁNDO USAR EXTENDED THINKING:
- Evaluaciones con múltiples criterios que interactúan
- Decisiones donde los trade-offs importan
- Análisis donde queremos auditar el razonamiento
- Scoring que debe ser defendible ante el cliente

CUÁNDO NO USARLO:
- Tareas simples (extracción, formato, resumen)
- Cuando la latencia importa más que la precisión
- Responses cortas o repetitivas
"""
import json
import anthropic

from config import ANTHROPIC_API_KEY, SCORER_MODEL, THINKING_BUDGET_TOKENS, ICP_DEFINITION, _HTTP_CLIENT
from models import CompanyResearch, ICPScore

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=_HTTP_CLIENT)


def run_scorer(research: CompanyResearch) -> tuple[ICPScore, int]:
    """
    Evalúa el fit ICP con Extended Thinking.
    Retorna (ICPScore, tokens_usados)
    """
    print(f"\n🧠 [Scorer] Evaluando ICP fit para: {research.company_name}")
    print(f"  → Thinking budget: {THINKING_BUDGET_TOKENS:,} tokens")

    system_prompt = f"""Eres un experto en ventas B2B y evaluación de ICP (Ideal Customer Profile).
Tu trabajo es evaluar si una empresa es un buen prospecto para nuestro producto.
Sé riguroso, objetivo y específico. No infles scores — un 6 real vale más que un 9 inventado.

{ICP_DEFINITION}"""

    user_prompt = f"""Evalúa el fit ICP de este prospecto basándote en la investigación recopilada.

=== DATOS DE INVESTIGACIÓN ===
Empresa: {research.company_name}
Dominio: {research.domain}
Descripción: {research.description}
Industria: {research.industry}
Tamaño estimado: {research.company_size_estimate}

Noticias recientes:
{chr(10).join(f"- {n}" for n in research.recent_news)}

Pain points detectados:
{chr(10).join(f"- {p}" for p in research.pain_points_detected)}

Tech stack:
{chr(10).join(f"- {t}" for t in research.tech_stack_hints)}

Señales sociales:
{chr(10).join(f"- {s}" for s in research.social_signals)}

Contacto: {research.contact_name} ({research.contact_role})
Actividad reciente: {research.contact_recent_activity or 'No disponible'}
Confianza del research: {research.research_confidence}

=== INSTRUCCIONES ===
Analiza profundamente cada criterio del ICP. Considera cómo interactúan entre sí.
Busca señales de compra implícitas (un post sobre IA + un rol de SDR = dolor real).

Devuelve SOLO un JSON válido con este esquema (sin markdown):
{{
  "score": <int 1-10>,
  "recommendation": "<proceed|nurture|discard>",
  "criteria_scores": {{
    "modelo_negocio_b2b": <int 1-10>,
    "equipo_ventas_activo": <int 1-10>,
    "dolor_en_prospeccion": <int 1-10>,
    "presupuesto_estimado": <int 1-10>,
    "accesibilidad_decisor": <int 1-10>
  }},
  "reasoning_summary": "<párrafo explicando el score general>",
  "key_insights": ["<insight 1>", "<insight 2>", "<insight 3>"],
  "objections_anticipated": ["<objeción probable 1>", "<objeción probable 2>"]
}}"""

    response = client.messages.create(
        model=SCORER_MODEL,
        max_tokens=16_000,  # IMPORTANTE: con thinking, max_tokens debe incluir el thinking budget
        thinking={
            "type": "adaptive",  # antes "enabled" — adaptive ajusta el budget según la complejidad
            "budget_tokens": THINKING_BUDGET_TOKENS
        },
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    total_tokens = response.usage.input_tokens + response.usage.output_tokens
    print(f"  → Tokens usados: {total_tokens:,} (thinking budget: {THINKING_BUDGET_TOKENS:,})")

    # Extraer el thinking y el texto de respuesta por separado
    thinking_text = None
    response_text = None

    for block in response.content:
        if block.type == "thinking":
            thinking_text = block.thinking
            print(f"  💭 Thinking generado: {len(thinking_text):,} chars")
        elif block.type == "text":
            response_text = block.text

    if not response_text:
        raise ValueError("Scorer no devolvió texto de respuesta")

    # Extraer JSON robusto (ignora texto alrededor)
    import re
    response_text = response_text.strip()
    if "```" in response_text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if match:
            response_text = match.group(1)
    if not response_text.startswith("{"):
        match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if match:
            response_text = match.group(0)

    score_data = json.loads(response_text.strip())

    # Adjuntamos preview del thinking (valioso para la demo)
    thinking_preview = None
    if thinking_text:
        thinking_preview = thinking_text[:600] + "..." if len(thinking_text) > 600 else thinking_text

    score_data["thinking_preview"] = thinking_preview

    icp_score = ICPScore(**score_data)
    print(f"  ✅ Score: {icp_score.score}/10 → {icp_score.recommendation.upper()}")

    return icp_score, total_tokens
