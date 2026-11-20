# Sesión de Content Rewards para el robot (secreto `CR_SESION`)

El robot (`fabrica/enviar_cr.py`, job `enviar_cr` de `fabrica.yml`) envía a Content Rewards los enlaces de los
clips de campaña en los 30 min siguientes a publicarlos. Necesita la sesión de la cuenta Guion Vivo.

Renovarla (cuando llegue el issue «ALERTA ROJA: Content Rewards…»):
1. En la PC: `node herramientas/sesion-cr.mjs` (la primera vez, `npm i playwright-core` en una carpeta temporal).
2. Se abre Chrome aparte: iniciar sesión en Content Rewards con correo y código.
3. La ventana se cierra sola, la sesión queda en el portapapeles y se abre GitHub en «New secret».
4. Name: `CR_SESION` · Secret: Ctrl+V · «Add secret» (si ya existe: abrir `CR_SESION` → «Update secret»).
5. Copiar cualquier otra cosa para limpiar el portapapeles.

Prueba sin enviar: Actions → `enviar-cr` → Run workflow (url + cr_campaign_id, «real» sin marcar).
