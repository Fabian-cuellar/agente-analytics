"""
Demo 4 — Agente 4: Output
- Gmail: envía el email generado a tu bandeja para revisión
- Google Sheets: registra el prospecto en una hoja real
- Haiku: genera el resumen de notificación
"""
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import anthropic

from config import (
    ANTHROPIC_API_KEY, OUTPUT_MODEL,
    SHEETS_MOCK_MODE, SHEETS_SPREADSHEET_ID, SHEETS_WORKSHEET_NAME,
    GMAIL_USER, GMAIL_APP_PASSWORD, GMAIL_TO,
    _HTTP_CLIENT
)
from models import CompanyResearch, ICPScore, EmailDraft, SheetRecord, OutputResult

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=_HTTP_CLIENT)


def run_output_agent(
    research: CompanyResearch,
    score: ICPScore,
    email: EmailDraft
) -> tuple[OutputResult, int]:
    print(f"\n📊 [Output] Registrando prospecto: {research.company_name}")

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

    sheet_written, sheet_row_id = _write_to_sheets(record)
    email_sent = _send_gmail(research, score, email)
    notification, tokens = _generate_notification(research, score, email)

    print(f"  ✅ Sheets: {'OK' if sheet_written else 'MOCK'} | Gmail: {'OK' if email_sent else 'SKIP'}")

    result = OutputResult(
        sheet_row_written=sheet_written,
        sheet_row_id=sheet_row_id,
        notification_sent=True,
        notification_summary=notification
    )
    return result, tokens


# ── GOOGLE SHEETS ──────────────────────────────────────────────────────────────

def _write_to_sheets(record: SheetRecord) -> tuple[bool, str]:
    if SHEETS_MOCK_MODE or not SHEETS_SPREADSHEET_ID:
        print(f"  [Sheets MOCK] Score: {record.icp_score}/10 | {record.recommendation}")
        return True, f"mock_row_{datetime.utcnow().strftime('%H%M%S')}"

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]

        # credentials.json debe estar en la raíz del proyecto
        # En Render: sube el contenido como variable de entorno GOOGLE_CREDENTIALS_JSON
        import os
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(creds_json)
                creds_path = f.name
        else:
            creds_path = "credentials.json"

        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEETS_SPREADSHEET_ID)
        ws = sh.worksheet(SHEETS_WORKSHEET_NAME)

        # Cabeceras si la sheet está vacía
        if ws.row_count == 0 or ws.acell('A1').value is None:
            ws.append_row([
                "Timestamp", "Empresa", "Contacto", "Cargo",
                "Score ICP", "Recomendación", "Asunto Email",
                "Preview Email", "Confianza Research", "Fuentes"
            ])

        row = [
            record.timestamp, record.company_name, record.contact_name,
            record.contact_role, record.icp_score, record.recommendation,
            record.email_subject, record.email_preview,
            record.research_confidence, record.sources_count
        ]
        result = ws.append_row(row)
        row_id = str(result.get("updates", {}).get("updatedRange", "nueva fila"))
        print(f"  ✅ Sheets: fila escrita → {row_id}")
        return True, row_id

    except Exception as e:
        print(f"  ⚠️  Sheets error: {e}")
        return False, None


# ── GMAIL ──────────────────────────────────────────────────────────────────────

def _send_gmail(research: CompanyResearch, score: ICPScore, email: EmailDraft) -> bool:
    """
    Envía el email generado a tu bandeja (GMAIL_TO) para revisión.
    No envía directamente al prospecto — tú decides si mandarlo.
    """
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("  [Gmail SKIP] Sin credenciales — agrega GMAIL_USER y GMAIL_APP_PASSWORD")
        return False

    try:
        score_emoji = "🔥" if score.score >= 8 else "👀" if score.score >= 6 else "❄️"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[ProspectFlow] {score_emoji} {research.company_name} — Score {score.score}/10"
        msg["From"]    = GMAIL_USER
        msg["To"]      = GMAIL_TO

        # Cuerpo HTML del email de notificación
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <div style="background:#1a1a2e;color:#e5e7eb;padding:20px;border-radius:8px 8px 0 0">
            <h2 style="margin:0">ProspectFlow {score_emoji}</h2>
            <p style="margin:4px 0 0;color:#9ca3af">Nuevo prospecto analizado</p>
          </div>

          <div style="background:#f9fafb;padding:20px;border:1px solid #e5e7eb">
            <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
              <tr>
                <td style="padding:8px;color:#6b7280;font-size:13px">Empresa</td>
                <td style="padding:8px;font-weight:600">{research.company_name}</td>
              </tr>
              <tr style="background:#fff">
                <td style="padding:8px;color:#6b7280;font-size:13px">Contacto</td>
                <td style="padding:8px;font-weight:600">{research.contact_name} · {research.contact_role}</td>
              </tr>
              <tr>
                <td style="padding:8px;color:#6b7280;font-size:13px">Score ICP</td>
                <td style="padding:8px;font-weight:800;font-size:18px;color:{'#16a34a' if score.score >= 8 else '#d97706' if score.score >= 6 else '#dc2626'}">{score.score}/10 — {score.recommendation.upper()}</td>
              </tr>
            </table>

            <div style="background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:16px;margin-bottom:16px">
              <p style="margin:0 0 8px;color:#6b7280;font-size:12px;text-transform:uppercase;letter-spacing:1px">Email generado listo para enviar</p>
              <p style="margin:0 0 8px;font-weight:600;color:#1f2937">Asunto: {email.subject}</p>
              <hr style="border:none;border-top:1px solid #f3f4f6;margin:10px 0">
              <p style="margin:0;white-space:pre-line;color:#374151;font-size:14px;line-height:1.6">{email.body}</p>
            </div>

            <div style="background:#eff6ff;border-left:4px solid #3b82f6;padding:12px">
              <p style="margin:0;font-size:13px;color:#1e40af"><strong>Razonamiento:</strong> {score.reasoning_summary[:300]}...</p>
            </div>
          </div>

          <div style="background:#f3f4f6;padding:12px;text-align:center;border-radius:0 0 8px 8px">
            <p style="margin:0;font-size:12px;color:#9ca3af">ProspectFlow · Sistema multiagente de prospección B2B</p>
          </div>
        </div>
        """

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        print(f"  ✅ Gmail enviado a {GMAIL_TO}")
        return True

    except Exception as e:
        print(f"  ⚠️  Gmail error: {e}")
        return False


# ── NOTIFICACIÓN (Haiku) ────────────────────────────────────────────────────────

def _generate_notification(
    research: CompanyResearch,
    score: ICPScore,
    email: EmailDraft
) -> tuple[str, int]:
    emoji = "🔥" if score.score >= 8 else "👀" if score.score >= 6 else "❄️"

    prompt = f"""Genera un mensaje de notificación corto (máximo 3 líneas) para el equipo de ventas
sobre este nuevo prospecto procesado. Sé directo y accionable.

Empresa: {research.company_name}
Contacto: {research.contact_name} ({research.contact_role})
Score ICP: {score.score}/10
Recomendación: {score.recommendation}
Insight clave: {score.key_insights[0] if score.key_insights else 'N/A'}
Asunto email: {email.subject}

Formato: texto plano, máximo 3 líneas, emoji {emoji} al inicio."""

    response = client.messages.create(
        model=OUTPUT_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}]
    )

    tokens = response.usage.input_tokens + response.usage.output_tokens
    return response.content[0].text.strip(), tokens
