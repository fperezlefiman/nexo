---
name: email-imap
description: "Read mail over IMAP with a per-profile account and app password (no Google Cloud, no OAuth)."
version: 1.0.0
---

# Correo por IMAP

Sirve para cualquier perfil que necesite leer correo: adjuntos, cartolas, notificaciones. No usa
OAuth ni proyectos de Google Cloud; va con usuario y contraseña de aplicación.

## Credenciales por perfil

Cada perfil define su cuenta en `~/.local/share/nexo/<perfil>/.env`:

```
GMAIL_USER=tu-correo@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
GMAIL_HOST=imap.gmail.com
```

Se leen de a una, nunca el archivo entero:

- Tool MCP: `credentials` con `{"profile": "<perfil>", "service": "gmail", "key": "app_password"}`.
- CLI: `nexo credentials get --profile <perfil> GMAIL_APP_PASSWORD`.

Para listar qué variables existen sin exponer valores: `nexo credentials list --profile <perfil>`.

## Reglas

- Una cuenta por perfil: dos perfiles pueden usar casillas distintas sin tocar el código.
- Contraseña de aplicación, no la del usuario. Si el proveedor exige OAuth, ese proveedor queda fuera.
- IMAP es solo lectura: no borres ni muevas mensajes salvo que el usuario lo pida.
- Un perfil puede tener varias cuentas (`GMAIL_USER_2`, `GMAIL_APP_PASSWORD_2`). Nombralas así.

## Dónde vive el caso complejo

El perfil `finance` ya resuelve descarga de cartolas, claves de PDF y deduplicación: su skill
`finance-cartolas` es la referencia. Esta skill es el patrón general; no la dupliques.
