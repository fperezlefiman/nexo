# ADR 0020 — Credenciales fuera del código, y la que estaba adentro se rotó

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Reemplaza**: —

## Contexto

Una revisión de código completa encontró que `src/nexo/db.py` tenía la URL de Postgres como valor por
defecto:

```python
DATABASE_URL = os.environ.get("NEXO_DATABASE_URL", "postgresql://nexo:****@127.0.0.1:5546/nexo")
```

Comprobado: el hash de esa clave coincide con `POSTGRES_PASSWORD` del contenedor. Es decir, la
contraseña real de la base estaba en un repositorio **público** desde el commit de F2. Un valor por
defecto con credenciales es una filtración con fecha de publicación.

## Decisión

1. **`db.py` no tiene credenciales ni valores por defecto.** `database_url()` lee `NEXO_DATABASE_URL`
   y, si no está, un archivo `~/.config/nexo/database_url` en modo 600. Si no encuentra ninguno,
   lanza `RuntimeError`: el proceso no arranca en un estado ambiguo.
2. **La contraseña se rotó** (`ALTER USER nexo WITH PASSWORD ...`) y la anterior quedó rechazada,
   verificado con un intento de conexión real.
3. **El secreto no se pasa por línea de comandos** ni por variable de entorno en unidades de systemd
   ni en el registro MCP del motor: el archivo 600 lo lee cada proceso que lo necesite, incluido el
   servidor MCP que el motor lanza como hijo.
4. **Se agregó `psycopg[binary]` a las dependencias** de `pyproject.toml`: la instalación documentada
   producía un entorno roto (`ModuleNotFoundError: psycopg`) porque la dependencia se había instalado
   a mano en el venv.

## Alternativas descartadas

- **Seguir con variable de entorno y `.env`**: funciona, pero el proceso hijo (MCP) no hereda el
  `.env` del worker; el archivo en disco resuelve los dos casos con una sola fuente.
- **Borrar el historial de git**: oculta el secreto sin invalidarlo. Rotar es lo que corta el riesgo;
  reescribir la historia queda como opcional y no se hizo.
- **Guardar la contraseña en la tabla `kv`**: exige una credencial para leer la credencial.

## Consecuencias

- Levantar el proyecto requiere un paso más (crear el archivo de secreto). Está en el README, tres
  líneas, y falla con un mensaje que dice exactamente qué falta.
- Cualquier proceso nuevo que hable con la base hereda la credencial sin configuración adicional.
- La contraseña anterior sigue en el historial público de GitHub. Después de la rotación es un valor
  muerto: no abre nada.
- `connect_timeout=10` quedó fijo en `db.py`: sin él, una base colgada bloqueaba el worker sin error.
