"""
Demo 4 — Agente de Prospección B2B
Configuración central
"""
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Cliente HTTP compartido — verify=False necesario en entornos con proxy SSL corporativo
# En producción (servidor propio) esto no es necesario, usar el default
import httpx
_HTTP_CLIENT = httpx.Client(verify=False)

# Modelos
INVESTIGATOR_MODEL = "claude-opus-4-6"   # tool use
SCORER_MODEL       = "claude-opus-4-6"   # extended thinking (requiere Opus)
WRITER_MODEL       = "claude-sonnet-4-6" # redacción — más barato, suficiente
OUTPUT_MODEL       = "claude-haiku-4-5-20251001"  # resumen liviano

# Extended Thinking budget para el Scorer
THINKING_BUDGET_TOKENS = 10_000

# ICP (Ideal Customer Profile) — ajustar por cliente
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

# Google Sheets (mock — conectar con gspread en producción)
SHEETS_MOCK_MODE = True  # True = solo imprime, False = escribe a Sheets real
SHEETS_SPREADSHEET_ID = os.getenv("SHEETS_SPREADSHEET_ID", "mock-spreadsheet-id")
SHEETS_WORKSHEET_NAME = "Prospectos"
