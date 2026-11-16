# 🦾 Max Steel Trading — sistema inteligente de compra-reventa de figuras

Plataforma para un negocio de figuras coleccionables **Max Steel** y **Dragon Ball GT**:
caza ofertas, las evalúa con IA (Claude), te alerta por Telegram, administra tu
inventario, prepara publicaciones de venta y calcula tus ganancias.

> **Principio de seguridad:** el sistema **nunca compra, nunca paga, nunca publica y nunca
> envía mensajes por ti.** Todo lo que toca a otras personas queda como borrador que tú
> apruebas y envías manualmente.

---

## 1. Arquitectura

```
                ┌───────────────────────── Tareas programadas (APScheduler) ─────────────────────────┐
                │  cada N min: ciclo de búsqueda      ·      9:07 diario: recordatorio de seguimientos │
                └──────────────┬──────────────────────────────────────────────────────────────────────┘
                               ▼
 eBay Browse API ──┐   ┌───────────────┐   ┌────────────────┐   ┌──────────────┐
 Mercado Libre API ├──▶│ 1. BUSCADOR   │──▶│ 2. EVALUADOR   │──▶│  Alertas     │──▶ Telegram + Panel
 Captura manual FB ┘   │ (deduplica,   │   │ Claude: foto + │   │ (score ≥ mín │
                       │  convierte $) │   │ texto → modelo,│   │  y ≤ presup.)│
                       └───────────────┘   │ estado, réplica│   └──────────────┘
                                           │ + valoración y │
                                           │ puntuación 0-100│
                                           └────────────────┘
   Tú decides ─▶ "Ya la compré" ─▶ ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
                                   │ 3. INVENTARIO│──▶│ 4. VENDEDOR  │──▶│ 5. ANALISTA  │
                                   │ costos, fotos│   │ borradores,  │   │ métricas,    │
                                   │ estado, venta│   │ precios,     │   │ qué comprar/ │
                                   └──────────────┘   │ paquetes,    │   │ qué vender   │
                                                      │ respuestas   │   └──────────────┘
                                                      └──────────────┘
          Base de datos: SQLite (inicio) o PostgreSQL  ·  Panel web: FastAPI + Jinja2
```

| Pieza | Archivo |
|---|---|
| Configuración (.env) | `app/config.py` |
| Base de datos y modelos | `app/db.py`, `app/models.py`, `app/seed.py` |
| Valoración y márgenes | `app/pricing.py` |
| Puntuación 0-100 | `app/scoring.py` |
| Cliente de Claude (salidas estructuradas) | `app/ia.py` |
| Agente 1 Buscador | `app/agents/buscador.py` |
| Agente 2 Evaluador | `app/agents/evaluador.py` |
| Agente 3 Inventario | `app/agents/inventario.py` |
| Agente 4 Vendedor | `app/agents/vendedor.py`, `app/agents/contacto.py` |
| Agente 5 Analista | `app/agents/analista.py` |
| Fuentes (APIs oficiales) | `app/sources/ebay.py`, `app/sources/mercadolibre.py` |
| Telegram | `app/notifications/telegram.py` |
| Tareas programadas | `app/scheduler.py` |
| Panel web + API JSON | `app/web/main.py`, `app/web/templates/` |
| Pruebas | `tests/` |

### Cómo se calcula la puntuación (0-100)

| Factor | Puntos máx. |
|---|---|
| Margen % (100 % o más = máximo) | 40 |
| Ganancia absoluta (relativa a tu presupuesto) | 15 |
| Dentro del presupuesto | 10 |
| Autenticidad (1 − riesgo de réplica no declarada) | 15 |
| Estado físico (sellado 10 … malo 0) | 10 |
| Confianza en la valoración (ventas reales > publicados > catálogo > IA) | 10 |

Topes: sobre presupuesto ≤ 40 · probable réplica **no declarada** ≤ 25 · margen negativo ≤ 20 ·
no es del nicho ≤ 10. Un **bootleg declarado** por el vendedor no se castiga como engaño
(tú también los coleccionas), pero queda marcado para no venderlo como original.

### Cómo se estima el valor de reventa
1. Mediana de **tus ventas reales** del modelo (últimos 180 días).
2. Si no hay: mediana de **precios publicados** (≥ 3) con 10 % de descuento.
3. Si no hay: **valor de referencia del catálogo** (lo pones tú).
4. Último recurso: estimación de **Claude** (baja confianza, se indica en el análisis).

El sistema **no inventa precios**: entre más ventas registres, más preciso será.

---

## 2. Instalación

Requisitos: Python 3.11 o superior.

```bash
cd max-steel-trading
```

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```
(En Windows: `.venv\Scripts\activate`)

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Edita `.env` con tus credenciales (sección 3) y luego:

```bash
python -m app.cli init
```

```bash
python -m app.cli servidor
```

Abre http://127.0.0.1:8000 (usuario y clave: `PANEL_USUARIO` / `PANEL_CLAVE`).

Para explorar el panel con **datos ficticios** antes de usar los tuyos:

```bash
python -m app.cli demo
```
(Después borra `data/maxsteel.db` para empezar limpio.)

Otros comandos:

```bash
python -m app.cli buscar
```

```bash
python -m app.cli metricas
```

```bash
python -m pytest -q
```

---

## 3. Credenciales y configuración que debes proporcionar

Todo va en el archivo `.env` (nunca lo compartas ni lo subas a git).

| Variable | ¿Obligatoria? | Dónde se obtiene |
|---|---|---|
| `ANTHROPIC_API_KEY` | Recomendada | https://console.anthropic.com → API Keys. Sin ella el sistema funciona con reglas por palabras clave (sin análisis de fotos). |
| `TELEGRAM_BOT_TOKEN` | Para alertas | En Telegram habla con **@BotFather** → `/newbot` → copia el token. |
| `TELEGRAM_CHAT_ID` | Para alertas | Envía cualquier mensaje a tu bot y abre `https://api.telegram.org/bot<TOKEN>/getUpdates`; copia `chat.id`. Pruébalo en Configuración → "Enviar mensaje de prueba". |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | Para buscar en eBay | https://developer.ebay.com → crea cuenta de desarrollador → *Application Keys* (Production). Solo se usa el token de aplicación, sin tu contraseña. |
| `EBAY_MARKETPLACE` | Opcional | `EBAY_US` (por defecto), `EBAY_ES`, etc. |
| `MELI_ACCESS_TOKEN` | Para buscar en Mercado Libre | https://developers.mercadolibre.com → crea una aplicación → obtén un access token con el flujo OAuth. **Caduca cada 6 horas**: renuévalo con el refresh token (ver sección 6). |
| `MELI_SITE_ID` | Opcional | `MLM` México, `MLA` Argentina, `MCO` Colombia, `MLC` Chile… |
| `MONEDA`, `TIPOS_CAMBIO` | Opcional | Tu moneda y tasas, p. ej. `USD=18.5` (los precios de eBay se convierten). |
| `PRESUPUESTO_MAXIMO`, `PUNTUACION_MINIMA_ALERTA` | Opcional | También editables desde el panel. |
| `COMISION_VENTA`, `COSTO_ENVIO` | Opcional | Para calcular la ganancia neta. |
| `PANEL_USUARIO`, `PANEL_CLAVE` | **Sí, si el panel es accesible desde internet** | Los eliges tú. |
| `DATABASE_URL` | Opcional | SQLite por defecto. PostgreSQL: `postgresql+psycopg://usuario:clave@host:5432/maxsteel` (instala `psycopg[binary]`). |
| `CLAUDE_MODEL`, `CLAUDE_EFFORT` | Opcional | Por defecto `claude-opus-5-5` con esfuerzo `medium`. |

**No necesitas (ni debes) dar:** contraseñas de Facebook, Mercado Libre o eBay, ni datos de pago.

### Configuración dentro del panel (primer uso)
1. **Configuración → Catálogo:** pon el *valor de referencia* de los modelos que conoces.
2. **Configuración → Precios de referencia:** agrega ventas reales que hayas visto (marca "precio de venta real").
3. **Configuración → Búsquedas:** ya vienen las 8 de tu lista; agrega o pausa términos.
4. **Vendedor → Canales:** agrega tus grupos de Max Steel y márcalos como autorizados (solo si eres miembro y las reglas permiten ventas).

---

## 4. Flujo de trabajo diario

1. El **Buscador** revisa eBay y Mercado Libre cada 30 min. Lo que ves en Facebook lo pegas en
   *Oportunidades → Agregar publicación de Facebook*, y se evalúa igual.
2. Recibes en **Telegram** las oportunidades con puntuación ≥ mínimo y dentro del presupuesto:
   foto, nombre, precio, reventa estimada, ganancia, ubicación, enlace y puntuación.
3. En la oportunidad: *Me interesa* → *Preparar borrador* de mensaje → *Autorizo contactar*
   → copias el mensaje y lo envías tú.
4. Si compras, pulsa **"Ya la compré"** con el costo real: entra al inventario con código
   (`MS-0001`, `DB-0001`), precio mínimo y recomendado.
5. En el producto o en **Vendedor**: genera el borrador de publicación (o un paquete),
   revísalo, publícalo tú en el grupo y marca "Ya la publiqué".
6. Cuando alguien te escriba, regístralo en **Prospectos** (con su consentimiento), pega su
   pregunta y el Vendedor prepara la respuesta. Tú la apruebas y la envías.
7. Al cerrar la venta: **Registrar venta** (con la casilla de confirmación). Esa venta alimenta
   los precios de mercado y las recomendaciones del Analista.

---

## 5. Seguridad y cumplimiento

| Regla | Cómo se garantiza |
|---|---|
| Nunca comprar ni pagar | No existe ningún código de compra o pago. "Ya la compré" solo registra algo que tú hiciste. |
| Autorización antes de contactar vendedores | Los mensajes son borradores; el estado `contacto_aprobado` lo pones tú, y el envío es manual. |
| Autorización antes de cerrar ventas | Registrar una venta exige marcar "Confirmo que yo cerré esta venta". |
| Sin mensajes masivos ni contacto no autorizado | Solo se preparan respuestas a prospectos con consentimiento; nunca se envían solos. Telegram solo escribe a **tu** chat. |
| Publicaciones solo en grupos de Max Steel | En código: un grupo de Facebook debe ser tema `max_steel`, estar autorizado, y no acepta figuras de otras líneas. |
| No guardar contraseñas de Facebook | No hay integración con cuentas de Facebook. |
| Respetar políticas de Meta | Meta no ofrece API para buscar en Marketplace ni en grupos (la Groups API se retiró en 2024). Por eso **no hay scraping**: Facebook es captura manual. |
| No evadir restricciones | Solo APIs oficiales con tus credenciales de desarrollador; si una API rechaza el acceso, se registra el error y no se reintenta por otras vías. |
| Panel protegido | Autenticación HTTP Basic con `PANEL_USUARIO`/`PANEL_CLAVE`. Para internet, ponlo detrás de HTTPS (Caddy, Nginx o Cloudflare Tunnel). |
| Auditoría | Cada acción de los agentes queda en *Registro*. |

---

## 6. Limitaciones conocidas del MVP y siguientes pasos

- **Mercado Libre:** el token dura 6 h. Siguiente paso: renovación automática con
  `MELI_CLIENT_ID`/`MELI_CLIENT_SECRET` y refresh token.
- **eBay:** la API Browse da precios publicados, no vendidos (el historial de vendidos
  requiere la Marketplace Insights API, de acceso restringido). Por eso tus ventas propias y
  los precios que registres son la fuente más fiable.
- **Facebook:** captura manual. Una mejora cómoda: un comando del bot de Telegram para
  reenviar enlaces y fotos desde el teléfono.
- **Valoración:** mejora con datos. Registra cada venta y algunos precios de referencia.
- **Despliegue:** para correr 24/7, usa un VPS pequeño o una Raspberry Pi con
  `python -m app.cli servidor --host 0.0.0.0` como servicio `systemd`, detrás de HTTPS.
- **Costos de IA:** cada evaluación con foto es una llamada a Claude. Si buscas muchos
  términos cada 30 min, sube `INTERVALO_BUSQUEDA_MIN` o baja `CLAUDE_EFFORT` a `low`.
