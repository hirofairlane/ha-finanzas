# HA Finanzas

Home Assistant **add-on** que ingesta extractos bancarios en Excel/HTML y te
da un panel visual (estilo comic, como Pool Brain / GOD Mode) con:

- Saldo global agregando **todas tus cuentas como si fueran una**.
- Vista por cuenta con saldo y movimientos.
- Desglose mensual por categoría con barras.
- **Provisiones** sugeridas (media 6/12 meses por categoría).
- Categorías **ilimitadas** con jerarquía de 3 niveles:
  1. **`gasto` / `ingreso`**
  2. **Tipo** (categoría libre: Supermercado, Suscripciones, Nómina…)
  3. **Detalle** (merchant / comercio: Mercadona, Amazon, Netflix…).
- **Deduplicación automática** de ficheros que se solapen (por hash SHA1
  del fichero **y** por hash de `(cuenta, fecha, importe, concepto)` línea a
  línea, así puedes subir extractos con periodos que se solapen sin miedo).
- **Detección de traspasos** entre cuentas propias: dos movimientos con
  importes opuestos ±0,01 € en un rango de ±3 días entre dos cuentas tuyas
  se marcan como traspaso y se excluyen del cómputo global.

## Bancos soportados

| Banco    | Formato                       | Estado |
|----------|-------------------------------|--------|
| Openbank | `Movimientos de Cuenta.xls` (XHTML iso-8859-1) | ✅ probado con 1000 movimientos |
| ING      | pendiente                     | 🚧 |
| BBVA     | pendiente                     | 🚧 |

Añadir un nuevo banco = crear un módulo en
`rootfs/opt/finanzas/parsers/<banco>.py` que exponga `sniff(path)` y
`parse(path) -> ParsedStatement`, y añadirlo a `_PARSERS` en
`parsers/__init__.py`.

## Uso

### 1. Instalar como add-on de HA

Copia esta carpeta al repositorio local de add-ons de tu HA (o publícalo en
GitHub y añádelo como repositorio en Supervisor). Instala **HA Finanzas** y
pulsa *Start*. La UI aparece en la barra lateral (`Finanzas`).

### 2. Importar extractos

Dos opciones:

- **UI → pestaña Importar**: sube el `.xls` desde el navegador.
- **Ingesta automática**: deja el fichero en
  `/share/ha_finanzas/inbox/`. El watcher lo procesa cada 10 s y lo mueve
  a `/share/ha_finanzas/archive/`.

### 3. Categorizar

El motor asigna categoría vía **reglas regex** (editables desde SQLite; una
CRUD web para reglas es TODO). Si no encuentra regla, extrae el comercio de
frases como `COMPRA EN <X>, CON LA TARJETA...`, `BIZUM A FAVOR DE <X>` y
lo memoriza. Un comercio recategorizado a mano hereda la categoría a
futuros movimientos con ese mismo merchant.

## Arquitectura

```
rootfs/opt/finanzas/
├── main.py           # aiohttp app + inbox watcher
├── api.py            # /api/* JSON endpoints
├── db.py             # SQLite schema + seed categorías/reglas
├── ingest.py         # dedup + categoriza + insert + empareja traspasos
├── categorize.py     # motor de reglas + extractor de merchant
├── parsers/
│   ├── types.py      # ParsedStatement / ParsedTransaction dataclasses
│   ├── openbank.py   # parser XHTML de Openbank
│   └── __init__.py   # auto-detección de parser
└── static/           # UI comic (index.html + style.css + app.js)
```

**SQLite** persistente en `/data/finanzas.db` (volumen del add-on).

## Desarrollo local (sin HA)

```bash
pip install --user --break-system-packages aiohttp beautifulsoup4 lxml
HA_FINANZAS_DB_PATH=/tmp/haf.db \
HA_FINANZAS_WATCH_SHARE_DIR=false \
HA_FINANZAS_PORT=18123 \
PYTHONPATH=rootfs/opt \
python3 -u -m finanzas.main
# → http://127.0.0.1:18123
```

Ingesta CLI:

```bash
PYTHONPATH=rootfs/opt HA_FINANZAS_DB_PATH=/tmp/haf.db \
python3 -c "from finanzas.ingest import ingest_file; \
print(ingest_file('Movimientos de Cuenta.xls'))"
```

## Roadmap

- [ ] CRUD web de reglas de categorización.
- [ ] Parsers ING / BBVA / Sabadell.
- [ ] Vista comparativa año-a-año.
- [ ] Exponer sensores a HA (saldo total, gasto del mes, provisión de
      categoría X) via MQTT o REST sensor.
- [ ] Alertas de gasto anormal (`gasto categoría X > media 6m + 2σ`).
- [ ] Icono / paleta por categoría desde la UI.

## Licencia

MIT.
