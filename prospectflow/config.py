"""
Demo 4 — ProspectFlow
Configuración central
"""
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Modelos
INVESTIGATOR_MODEL = "claude-opus-4-6"
SCORER_MODEL       = "claude-opus-4-6"   # Extended Thinking requiere Opus
WRITER_MODEL       = "claude-sonnet-4-6"
OUTPUT_MODEL       = "claude-haiku-4-5-20251001"

# Extended Thinking budget
THINKING_BUDGET_TOKENS = 10_000

# ── SERPER (búsqueda web real) ─────────────────────────────────────────────
SERPER_API_KEY = os.getenv("SERPER_API_KEY")  # gratis en serper.dev — 2500/mes

# ── GMAIL ──────────────────────────────────────────────────────────────────
GMAIL_USER         = os.getenv("GMAIL_USER")          # tu email gmail
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")  # contraseña de aplicación
GMAIL_TO           = os.getenv("GMAIL_TO", GMAIL_USER)  # a dónde llega la copia (por defecto a ti mismo)

# ── GOOGLE SHEETS ──────────────────────────────────────────────────────────
SHEETS_MOCK_MODE      = os.getenv("SHEETS_MOCK_MODE", "true").lower() == "true"
SHEETS_SPREADSHEET_ID = os.getenv("SHEETS_SPREADSHEET_ID", "")
SHEETS_WORKSHEET_NAME = "Prospectos"

# ── ICP DEFINITION (prompt cacheado) ───────────────────────────────────────
ICP_DEFINITION = """
Empresa objetivo para nuestro sistema multiagente de prospección B2B:

PERFIL IDEAL:
- Empresas B2B en LATAM que venden a otras empresas
- Equipos de ventas de 3-50 personas
- Ticket promedio > USD 500/mes o venta consultiva
- Usan CRM (HubSpot, Salesforce, Pipedrive)
- Proceso de outbound activo (SDRs, BDRs, founders vendiendo)
- Pain point: mucho tiempo en investigar leads, emails genéricos, baja tasa de respuesta

CRITERIOS DE SCORE:
- 9-10: Fit perfecto, dolor evidente, presupuesto claro, decisor accesible
- 7-8: Buen fit, uno o dos criterios no confirmados
- 5-6: Fit parcial, requiere más investigación
- 3-4: Fit bajo, posible pero no prioritario
- 1-2: No es el perfil, descartar

PRODUCTO QUE OFRECEMOS:
Sistema multiagente de prospección B2B que automatiza: investigación de empresas,
scoring de fit, redacción de emails hiperpersonalizados y registro en CRM.
Setup USD 3.000-5.000 + retainer USD 300-500/mes.
"""

# ── HTTP CLIENT (solo necesario en entornos con proxy SSL corporativo) ─────
import httpx
_HTTP_CLIENT = httpx.Client(verify=False)
