"""
Demo 4 — Agente 4: Output
Registra en Google Sheets y genera notificación de resumen.

Este agente es el más simple — no necesita LLM para la lógica principal.
Usa Haiku solo para generar el resumen de notificación (tarea liviana).

INTEGRACIÓN REAL CON GOOGLE SHEETS:
pip install gspread google-auth
Ver comentarios en _write_to_sheets_real() para el código de producción.
"""
from datetime import datetime
import anthropic

from config import (
    ANTHROPIC_API_KEY, OUTPUT_MODEL,
    SHEETS_MOCK_MODE, SHEETS_SPREADSHEET_ID, SHEETS_WORKSHEET_NAME,
    _HTTP_CLIENT
)
from models import CompanyResearch, ICPScore, EmailDraft, SheetRecord, OutputResult

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=_HTTP_CLIENT)


def run_output_agent(
    research: CompanyResearch,
    score: ICPScore,
    email: EmailDraft
) -> tuple[OutputResult, int]:
    """
    Registra el prospecto y genera notificación.
    Retorna (OutputResult, tokens_usados)
    """
    print(f"\n📊 [Output] Registrando prospecto: {research.company_name}")

    # 1. Construir registro para Sheets
    record = SheetRecord(
        timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        company_name=research.company_name,
        contact_name=research.contact_name,
        contact_role=research.contact_role,
        icp_score=score.score,
        recommendation=score.recommendation,
        email_subject=email.subject,
        email_preview=email.body[:150] + "..." if len(email.body) > 150 else email.body,
        research_confidence=research.research_confidence,
        sources_count=len(research.sources_used)
    )

    # 2. Escribir a Sheets (mock o real)
    sheet_written, sheet_row_id = _write_to_sheets(record)

    # 3. Generar notificación con Haiku (rápido y barato)
    notification, tokens = _generate_notification(research, score, email)

    print(f"  ✅ Registrado en Sheets: {sheet_row_id}")
    print(f"  ✅ Notificación generada ({len(notification)} chars)")

    result = OutputResult(
        sheet_row_written=sheet_written,
        sheet_row_id=sheet_row_id,
        notification_sent=True,
        notification_summary=notification
    )

    return result, tokens


def _write_to_sheets(record: SheetRecord) -> tuple[bool, str]:
    """
    Escribe una fila en Google Sheets.
    MOCK MODE: solo imprime y devuelve ID simulado.
    PRODUCCIÓN: descomentar el código real.
    """
    if SHEETS_MOCK_MODE:
        print(f"  [MOCK Sheets] Fila que se escribiría:")
        print(f"    Score: {record.icp_score}/10 | Rec: {record.recommendation}")
        print(f"    Email: {record.email_subject}")
        mock_row_id = f"row_{datetime.utcnow().strftime('%H%M%S')}"
        return True, mock_row_id

    # ── CÓDIGO REAL (descomentar en producción) ────────────────────────────
    # import gspread
    # from google.oauth2.service_account import Credentials
    #
    # scopes = ["https://spreadsheets.google.com/feeds",
    #           "https://www.googleapis.com/auth/drive"]
    # creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    # gc = gspread.authorize(creds)
    # sh = gc.open_by_key(SHEETS_SPREADSHEET_ID)
    # ws = sh.worksheet(SHEETS_WORKSHEET_NAME)
    #
    # row = [
    #     record.timestamp, record.company_name, record.contact_name,
    #     record.contact_role, record.icp_score, record.recommendation,
    #     record.email_subject, record.email_preview,
    #     record.research_confidence, record.sources_count
    # ]
    # result = ws.append_row(row)
    # row_id = str(result["updates"]["updatedRange"])
    # return True, row_id
    # ──────────────────────────────────────────────────────────────────────

    return False, None


def _generate_notification(
    research: CompanyResearch,
    score: ICPScore,
    email: EmailDraft
) -> tuple[str, int]:
    """
    Genera un resumen de notificación usando Haiku.
    Se enviaría por Slack/email/webhook en producción.
    Haiku: suficiente para este resumen, 10x más barato que Sonnet.
    """
    emoji = "🔥" if score.score >= 8 else "👀" if score.score >= 6 else "❄️"

    prompt = f"""Genera un mensaje de notificación corto (máximo 3 líneas) para el equipo de ventas
sobre este nuevo prospecto procesado. Sé directo y accionable.

Empresa: {research.company_name}
Contacto: {research.contact_name} ({research.contact_role})
Score ICP: {score.score}/10
Recomendación: {score.recommendation}
Insight clave: {score.key_insights[0] if score.key_insights else 'N/A'}
Asunto email generado: {email.subject}

Formato: texto plano, máximo 3 líneas, usa el emoji {emoji} al inicio."""

    response = client.messages.create(
        model=OUTPUT_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}]
    )

    tokens = response.usage.input_tokens + response.usage.output_tokens
    notification = response.content[0].text.strip()

    # En producción: enviar por Slack, webhook, email
    # slack_client.chat_postMessage(channel="#ventas-prospectos", text=notification)

    return notification, tokens
