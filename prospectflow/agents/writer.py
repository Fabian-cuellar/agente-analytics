"""
Demo 4 — Agente 3: Redactor
Genera email hiperpersonalizado basado en research + score.
Usa claude-sonnet: calidad suficiente para redacción, mucho más barato que opus.

La personalización real viene de:
1. Un trigger específico (post de LinkedIn, noticia reciente, cambio de cargo)
2. Dolor concreto detectado en el research (no genérico)
3. Conexión personal si existe (Fabián trabajó con Lucas en Rankmi)
4. Social proof relevante para la industria/tamaño del prospecto

Este agente NO usa tool use ni extended thinking — es una tarea de redacción
clara. Usar las features correctas para cada tarea es clave en producción.
"""
import json
import anthropic

from config import ANTHROPIC_API_KEY, WRITER_MODEL, _HTTP_CLIENT
from models import CompanyResearch, ICPScore, EmailDraft, ProspectRequest

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=_HTTP_CLIENT)


def run_writer(
    request: ProspectRequest,
    research: CompanyResearch,
    score: ICPScore
) -> tuple[EmailDraft, int]:
    """
    Genera el email hiperpersonalizado.
    Retorna (EmailDraft, tokens_usados)
    """
    print(f"\n✉️  [Redactor] Generando email para: {research.contact_name}")

    system_prompt = """Eres un experto en cold email B2B con 10 años de experiencia.
Escribes emails que consiguen respuesta porque son ESPECÍFICOS, no genéricos.

REGLAS DE ORO:
1. Primera línea: algo que demuestre que estudiaste a esta persona específica (no "vi tu empresa")
2. Dolor: conecta el dolor detectado con lo que ofrecemos — sé concreto, no vago
3. Prueba de valor: número concreto o resultado específico, no "mejoramos tu proceso"
4. CTA: una pregunta simple, no "me gustaría tener una llamada para presentarles..."
5. Largo: máximo 150 palabras. Los buenos cold emails son cortos.
6. Tono: humano, directo, nunca robótico ni corporativo
7. Si hay conexión personal — ÚSALA en la primera línea. Es el hook más poderoso.

IDIOMA Y ACENTO — MUY IMPORTANTE:
- Escribe en español chileno. Usa "tú" (no "vos"). Usa "te", "tu", "tienes" (nunca "tenés", "vos tenés").
- Ejemplos: "¿Te interesa verlo?" ✅ | "¿Te interesa?" ✅ | "¿Te gustaría?" ✅
- NUNCA: "¿te interesaría verlo?" con voseo, "vos", "tenés", "hacés", "querés"
- El tono es directo y chileno, no formal ni argentino.

LO QUE NUNCA DEBES HACER:
- "Espero que estés bien"
- "Me pongo en contacto porque..."
- "Nuestra solución de vanguardia..."
- "¿Tienes 15 minutos para una llamada?"
- Escribir más de 150 palabras
- Usar voseo argentino"""

    # Construimos el contexto completo para el redactor
    insights_text = "\n".join(f"- {i}" for i in score.key_insights)
    pain_points_text = "\n".join(f"- {p}" for p in research.pain_points_detected)
    signals_text = "\n".join(f"- {s}" for s in research.social_signals)
    objections_text = "\n".join(f"- {o}" for o in score.objections_anticipated)

    user_prompt = f"""Escribe un cold email B2B altamente personalizado.

=== CONTEXTO DEL REMITENTE ===
Nombre: Fabián Cuellar
Empresa: [Tu empresa/proyecto de agentes IA]
Propuesta: Sistema multiagente que automatiza prospección B2B completa
  (investigación + scoring + email personalizado + CRM) end-to-end.
  Setup USD 3.000-5.000 + retainer USD 300-500/mes.
Conexión personal: Fabián y Lucas trabajaron juntos en Rankmi.

=== PROSPECTO ===
Nombre: {research.contact_name}
Cargo: {research.contact_role}
Empresa: {research.company_name} ({research.domain})
Descripción: {research.description}
Tamaño: {research.company_size_estimate} personas

=== SEÑALES DETECTADAS (usarlas para personalizar) ===
Dolor principal detectado:
{pain_points_text}

Actividad reciente del contacto:
{research.contact_recent_activity or 'No disponible'}

Señales sociales:
{signals_text}

Noticias recientes empresa:
{chr(10).join(f"- {n}" for n in research.recent_news)}

=== INSIGHTS DEL SCORER (ICP Score: {score.score}/10) ===
{insights_text}

=== OBJECIONES PROBABLES (para evitar en el email) ===
{objections_text}

=== INSTRUCCIÓN ===
Devuelve SOLO un JSON válido (sin markdown):
{{
  "subject": "<asunto específico y atractivo>",
  "body": "<cuerpo del email — máximo 150 palabras>",
  "subject_alternatives": ["<alternativa 1>", "<alternativa 2>"],
  "personalization_elements": ["<elemento usado 1>", "<elemento usado 2>", "..."],
  "tone": "<consultivo|directo|casual>",
  "word_count": <número>
}}

El email debe sentirse escrito por un humano que realmente estudió a Lucas,
no por un sistema automatizado. La paradoja de la automatización inteligente:
el resultado debe parecer que tardaste 30 minutos escribiéndolo."""

    response = client.messages.create(
        model=WRITER_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    total_tokens = response.usage.input_tokens + response.usage.output_tokens

    import re
    response_text = response.content[0].text.strip()

    # Extraer JSON robusto
    if "```" in response_text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if match:
            response_text = match.group(1)
    if not response_text.startswith("{"):
        match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if match:
            response_text = match.group(0)

    email_data = json.loads(response_text.strip())
    email = EmailDraft(**email_data)

    print(f"  ✅ Email generado. Asunto: '{email.subject}'")
    print(f"  → Palabras: {email.word_count} | Personalización: {len(email.personalization_elements)} elementos")

    return email, total_tokens
