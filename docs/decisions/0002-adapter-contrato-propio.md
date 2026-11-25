# 0002 — Adapter con contrato propio y snapshot del OpenAPI

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El motor elegido (ADR 0001) expone su API como **experimental, versión 0.0.1**. Su forma es la de una
herramienta interna publicada por comodidad: nombres de campos que pueden cambiar, endpoints que se
mueven, y un conjunto de rutas `/api/experimental/*` que por definición no tiene garantía. Además el
servidor exige autenticación (`/openapi.json` → `401 Authentication required`), así que hay un token
en juego que también puede cambiar de forma.

Un upgrade del motor, hecho por otra persona y sin aviso, no puede romper el dominio del sistema.

## Decisión

Todo el contacto con el motor vive en `adapters/opencode`, detrás del port `AgentRuntime`, con:

1. **Taxonomía de errores propia**: `RuntimeUnavailable`, `AuthFailed`, `SessionLost`,
   `ModelRateLimited`, `ModelUnavailable`, `ContextOverflow`, `PermissionRequested`, `ToolFailed`,
   `Unknown`. El dominio nunca ve un código HTTP ni un string del motor.
2. **Snapshot del contrato** en `contracts/opencode-openapi.snapshot.json`, con un test que falla
   cuando el servidor expone una forma distinta.
3. **Contract tests contra el servidor real** y **suite offline con fixtures grabadas**, para que el
   CI no dependa de tener el motor levantado.
4. **Regla dura:** ninguna capacidad central del producto se cuelga de un endpoint `/experimental/*`.
   Si un endpoint así desaparece, se pierde una comodidad, no una función.

## Consecuencias

- **Se gana:** el upgrade del motor se convierte en un evento observable (test rojo) y no en una caída
  en producción; el dominio se testea sin red; el trabajo de adaptación queda acotado a un directorio.
- **Cuesta:** una capa de traducción que hay que mantener (dos modelos de datos parecidos, uno
  nuestro y uno ajeno); ~150–250 líneas de código "aburrido" que no lucen en ningún demo.
- **Se vuelve difícil:** usar features nuevas del motor sin pasar por el port (lo cual es el punto).
- **Verificación:** el test del snapshot corre en CI; un `grep` de `httpx` fuera de `adapters/` tiene
  que dar cero (regla de imports verificable).

## Alternativas rechazadas

- **Usar los tipos generados del OpenAPI directamente en el dominio** — máximo acoplamiento justo en
  el componente más inestable; un rename de campo se propaga a toda la aplicación.
- **Vendorizar el motor (fork)** — se asume el mantenimiento de un proyecto ajeno para evitar una capa
  de 200 líneas; costo desproporcionado.
- **Confiar en semver y "no va a cambiar"** — la propia API se declara experimental; es exactamente el
  caso donde no se puede confiar.

## Lo que habilita en una entrevista

"Integré una API marcada experimental: contrato propio, errores traducidos, snapshot de contrato en
CI y una regla de no depender de endpoints experimentales para nada central. Cuando el proveedor
cambie, el test me avisa y adapto un archivo." Es la respuesta de quien ya integró un proveedor
inestable en producción.
