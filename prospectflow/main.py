"""
Demo 4 — Sistema Multiagente de Prospección B2B
Orchestrator + FastAPI

Uso:
  uvicorn main:app --reload --port 8004

Test:
  curl -X POST http://localhost:8004/prospect \
    -H "Content-Type: application/json" \
    -d '{"company_name": "growclub.io", "contact_name": "Lucas Clavero", "contact_role": "CEO"}'

O desde Python:
  python main.py  (corre el pipeline directamente sin servidor)
"""
import time
import asyncio
from datetime import datetime

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from config import ANTHROPIC_API_KEY
from models import ProspectRequest, ProspectResult
from agents import run_investigator, run_scorer, run_writer, run_output_agent


# ── FASTAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ProspectFlow API",
    description="Sistema multiagente de prospección B2B — 4 agentes con Claude SDK",
    version="1.0.0"
)

# CORS — permite que Netlify (u otro frontend) llame a esta API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # En producción: reemplaza por tu URL de Netlify exacta
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "status": "online",
        "demo": "Demo 4 — Prospección B2B Multiagente",
        "agentes": ["Investigador (tool use)", "Scorer (Extended Thinking)", "Redactor", "Output (Sheets)"],
        "uso": "POST /prospect con {company_name, contact_name, contact_role}"
    }


@app.post("/prospect", response_model=None)
async def prospect_company(request: ProspectRequest):
    """
    Pipeline completo: empresa → investigación → scoring → email → registro.
    Tiempo estimado: 30-60 segundos (Extended Thinking toma tiempo).
    """
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, run_pipeline, request
        )
        return JSONResponse(content=result.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    """Verifica que la API key de Anthropic esté configurada."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY no configurada")
    return {"status": "healthy", "api_key_configured": True}


# ── ORCHESTRATOR ──────────────────────────────────────────────────────────────

def run_pipeline(request: ProspectRequest) -> ProspectResult:
    """
    Orquesta los 4 agentes en secuencia.
    Cada agente recibe el output del anterior — esto es multiagente básico.

    Para multiagente más avanzado (paralelo, con retry, con estado compartido)
    → Claude Agent SDK o frameworks como LangGraph.
    Pero para este caso de uso lineal, esto es suficiente y más simple de mantener.
    """
    print(f"\n{'='*60}")
    print(f"🚀 PIPELINE INICIADO")
    print(f"   Empresa:  {request.company_name}")
    print(f"   Contacto: {request.contact_name} ({request.contact_role or '?'})")
    print(f"   Hora:     {datetime.utcnow().isoformat()}")
    print(f"{'='*60}")

    start_time = time.time()
    total_tokens = 0

    # ── AGENTE 1: INVESTIGADOR ─────────────────────────────────────────────
    research, tokens_1 = run_investigator(request)
    total_tokens += tokens_1

    # ── AGENTE 2: SCORER ──────────────────────────────────────────────────
    score, tokens_2 = run_scorer(research)
    total_tokens += tokens_2

    # DECISIÓN: si el score es muy bajo, no perdemos tokens en redactar
    if score.score <= 2:
        print(f"\n⛔ Score {score.score}/10 — prospecto descartado. No se generará email.")
        # En producción podrías igualmente registrarlo en Sheets como "descartado"

    # ── AGENTE 3: REDACTOR ────────────────────────────────────────────────
    email, tokens_3 = run_writer(request, research, score)
    total_tokens += tokens_3

    # ── AGENTE 4: OUTPUT ──────────────────────────────────────────────────
    output, tokens_4 = run_output_agent(research, score, email)
    total_tokens += tokens_4

    duration = time.time() - start_time

    # ── RESULTADO FINAL ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"✅ PIPELINE COMPLETADO en {duration:.1f}s")
    print(f"   Total tokens: {total_tokens:,}")
    print(f"   Costo estimado: ~USD {total_tokens * 0.000015:.4f}")  # approx Opus pricing
    print(f"{'='*60}")
    print(f"\n📧 EMAIL GENERADO:")
    print(f"   Asunto: {email.subject}")
    print(f"   ---")
    print(email.body)
    print(f"\n🔔 NOTIFICACIÓN:")
    print(output.notification_summary)
    print(f"\n💡 THINKING PREVIEW (Extended Thinking del Scorer):")
    if score.thinking_preview:
        print(score.thinking_preview[:400])

    return ProspectResult(
        request=request,
        research=research,
        score=score,
        email=email,
        output=output,
        pipeline_duration_seconds=round(duration, 2),
        total_tokens_used=total_tokens
    )


# ── MODO DIRECTO (sin servidor) ───────────────────────────────────────────────

if __name__ == "__main__":
    """
    Corre el pipeline directamente para testear sin levantar el servidor.
    Útil para desarrollo y para la demo.
    """
    import json

    # Caso de prueba: Lucas Clavero / growclub.io
    request = ProspectRequest(
        company_name="growclub.io",
        contact_name="Lucas Clavero",
        contact_role="CEO",
        extra_context=(
            "Fabián trabajó con Lucas en Rankmi (HR-tech chilena). "
            "Lucas publicó hace 18 días en LinkedIn preguntando por soluciones de IA "
            "para automatizar prospección. Critica que todo lo que vio es muy genérico."
        )
    )

    result = run_pipeline(request)

    # Guardar resultado completo en JSON
    output_file = "prospect_result.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)

    print(f"\n💾 Resultado completo guardado en: {output_file}")
