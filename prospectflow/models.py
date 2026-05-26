"""
Demo 4 — Modelos Pydantic
Contratos entre agentes: cada agente recibe y devuelve tipos definidos.
Esto es lo que hace escalable un sistema multiagente real.
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ── INPUT ──────────────────────────────────────────────────────────────────────

class ProspectRequest(BaseModel):
    """Lo que recibe el endpoint POST /prospect"""
    company_name: str = Field(..., description="Nombre o dominio de la empresa, ej: 'growclub.io'")
    contact_name: str = Field(..., description="Nombre del contacto principal, ej: 'Lucas Clavero'")
    contact_role: Optional[str] = Field(None, description="Cargo del contacto, ej: 'CEO'")
    extra_context: Optional[str] = Field(None, description="Contexto adicional que ya sabemos")


# ── AGENTE 1: INVESTIGADOR ─────────────────────────────────────────────────────

class CompanyResearch(BaseModel):
    """Output del Agente Investigador"""
    company_name: str
    domain: str
    description: str = Field(..., description="Qué hace la empresa en 2-3 oraciones")
    industry: str
    company_size_estimate: str  # "1-10", "11-50", "51-200", etc.

    # Señales de negocio (lo que hace el email relevante)
    recent_news: list[str] = Field(default_factory=list, description="Noticias o eventos recientes")
    pain_points_detected: list[str] = Field(default_factory=list, description="Dolores inferidos del research")
    tech_stack_hints: list[str] = Field(default_factory=list, description="Tech que usan (inferido)")
    social_signals: list[str] = Field(default_factory=list, description="Posts, contenido, actividad LinkedIn")

    # Contacto
    contact_name: str
    contact_role: str
    contact_linkedin_url: Optional[str] = None
    contact_recent_activity: Optional[str] = None  # último post, charla, etc.

    # Metadata
    sources_used: list[str] = Field(default_factory=list)
    research_confidence: str = Field(..., description="high/medium/low — qué tan completo está el research")


# ── AGENTE 2: SCORER ──────────────────────────────────────────────────────────

class ICPScore(BaseModel):
    """Output del Agente Scorer — Extended Thinking"""
    score: int = Field(..., ge=1, le=10, description="Score ICP del 1 al 10")
    recommendation: str = Field(..., description="proceed / nurture / discard")

    # Breakdown por criterio
    criteria_scores: dict[str, int] = Field(
        default_factory=dict,
        description="Score por criterio: {criterio: score_1_10}"
    )

    # Lo más valioso del Extended Thinking: el razonamiento visible
    reasoning_summary: str = Field(..., description="Por qué este score — resumen del thinking")
    key_insights: list[str] = Field(..., description="3-5 insights clave para personalizar el email")
    objections_anticipated: list[str] = Field(default_factory=list, description="Objeciones probables")

    # El thinking raw de Claude (para mostrar en demo)
    thinking_preview: Optional[str] = Field(None, description="Primeras 500 chars del thinking de Claude")


# ── AGENTE 3: REDACTOR ────────────────────────────────────────────────────────

class EmailDraft(BaseModel):
    """Output del Agente Redactor"""
    subject: str = Field(..., description="Asunto del email — específico, no genérico")
    body: str = Field(..., description="Cuerpo del email en texto plano")

    # Para A/B testing o variantes
    subject_alternatives: list[str] = Field(default_factory=list, description="2 asuntos alternativos")

    # Metadata de calidad
    personalization_elements: list[str] = Field(
        ..., description="Lista de elementos personalizados usados (para auditoría)"
    )
    tone: str = Field(..., description="Tono del email: consultivo/directo/casual")
    word_count: int


# ── AGENTE 4: OUTPUT ──────────────────────────────────────────────────────────

class SheetRecord(BaseModel):
    """Fila que se escribe en Google Sheets"""
    timestamp: str
    company_name: str
    contact_name: str
    contact_role: str
    icp_score: int
    recommendation: str
    email_subject: str
    email_preview: str  # primeras 150 chars del body
    research_confidence: str
    sources_count: int


class OutputResult(BaseModel):
    """Output del Agente Output"""
    sheet_row_written: bool
    sheet_row_id: Optional[str] = None  # row ID en Sheets
    notification_sent: bool
    notification_summary: str  # resumen para Slack/email/webhook


# ── RESULTADO FINAL ───────────────────────────────────────────────────────────

class ProspectResult(BaseModel):
    """Resultado completo del pipeline — lo que devuelve POST /prospect"""
    request: ProspectRequest
    research: CompanyResearch
    score: ICPScore
    email: EmailDraft
    output: OutputResult

    # Metadata del pipeline
    pipeline_duration_seconds: float
    total_tokens_used: int
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
