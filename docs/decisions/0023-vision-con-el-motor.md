# ADR 0023 — `vision_analyze` se resuelve con el motor, no con un proveedor nuevo

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Desbloquea**: Corte 4 (`freelance`) y los cortes 5 y 6

## Contexto

La evaluación del Corte 4 encontró que `vision_analyze` —ver una captura, leer un aviso, mirar media
generada— es la única capacidad sin reemplazo en `nexo`, y que no es exclusiva de `freelance`: los
perfiles que la usan la llamaron cientos de veces, y sin ella se pierde una capacidad de uso diario.

El dato que cambia la decisión no estaba en el diseño: **el motor ya puede leer imágenes.** Probado el
2026-11-24 — sesión abierta con `gemini-4-neon`, pedido "leé este PNG y decime el color", respuesta
correcta. El agente usa su propia tool de lectura de archivos sobre el PNG y el modelo multimodal hace
el resto. No hace falta integrar nada nuevo.

## Alternativas

1. **API de visión aparte (proveedor nuevo).** Rechazada: una clave más, un secreto más para rotar, un
   costo que no se factura junto al resto, y el mismo trabajo que ya hace el motor.
2. **Modelo local (BLIP, OCR + clasificador).** Rechazada: OCR resuelve texto, no "¿qué oferta es esta y
   cuánto paga?"; y un modelo de visión local decente es infraestructura que este homelab no tiene.
3. **Solo OCR (`tesseract`).** Rechazada por la misma razón: el caso de uso es entender la imagen, no
   transcribirla. Un aviso de trabajo tiene capturas con texto, iconos y layout.
4. **Tool MCP que abre una sesión con un modelo multimodal del motor.** Es la elegida.

## Decisión

`vision_analyze(source, question, model?)` como tool MCP (`src/nexo/vision.py`, handler en `mcp.py`):

- Acepta ruta local o URL `http(s)`. La URL se descarga a un temporal con sufijo real.
- Rechaza rutas inexistentes, extensiones fuera de `png/jpg/jpeg/webp/gif` y archivos de más de 12 MB.
- Abre sesión con el primer modelo multimodal disponible: **gratuito primero**
  (`nemotron-3.5-super-vl-free`), **de pago como fallback** (`gemini-4-neon`), override por
  `NEXO_VISION_MODEL`.
- Devuelve `{text, model, path}` — el modelo que efectivamente respondió queda registrado, igual que en
  el resto del sistema.

## Consecuencias

- **Cada llamada es un turno completo**: ~8–20 s y, cuando la ventana gratuita está agotada, costo del
  modelo de pago. No sirve para procesar 200 imágenes; para eso, un script con bash.
- **La cadena de fallback es corta y explícita**: si el gratuito está limitado, se usa el de pago y queda
  anotado en la respuesta. Si ambos fallan, el error dice qué modelos se intentaron.
- El archivo tiene que ser legible por el proceso del motor — mismo usuario, misma máquina. Una ruta
  fuera del workspace funciona hoy porque el motor corre con el usuario del operador; cuando pase a un usuario
  dedicado (deuda de seguridad declarada en `operations.md`), esto hay que revisarlo.
- **El gap del Corte 4 queda cerrado**; el inventario de `migration.md` §4.4 pasa a "paridad" en esa
  fila. El bloqueante que queda es `execute_code` (2.290 llamadas a heredoc).
