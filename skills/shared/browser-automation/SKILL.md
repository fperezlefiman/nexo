---
name: browser-automation
description: "Headless browser per account: nexo browse, per-profile sessions, and the executable trap."
version: 1.0.0
---

# Navegador headless

Herramienta compartida para páginas que requieren JavaScript: portales de empleo, retail, paneles.

## Lo primero: `nexo browse`

```
nexo browse <url> --wait "<css selector>" --text
```

Trae el HTML ya renderizado o solo el texto. Para una lectura simple no hace falta más.

## Cuentas y sesiones distintas por perfil

Cada perfil guarda su perfil de navegador en `~/.local/share/nexo/<perfil>/` (por ejemplo
`workana-profile/`). Nunca compartas el directorio de sesión entre dos cuentas: mezcla cookies y
termina en cierres de sesión.

- Arranca siempre con un `user-data-dir` propio del perfil.
- Si la sesión caduca, se vuelve a iniciar a mano; no automatices el login con credenciales
  guardadas salvo que el usuario lo autorice.

## Trampa conocida

Chromium de Playwright se resuelve bajo el `HOME` del proceso. Si el navegador no arranca aunque el
binario exista, exportá `AGENT_BROWSER_EXECUTABLE_PATH` al binario real (o a un symlink estable) en
el `.env` del perfil. Un update de Playwright cambia la ruta con versión; el symlink no.

## Límites

- Respeta `robots`/rate limits del sitio; un bloqueo se reporta, no se fuerza con proxies rotativos.
- No corras navegadores en paralelo sobre el mismo `user-data-dir`.
