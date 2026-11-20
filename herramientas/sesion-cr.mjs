// Saca la sesión de Content Rewards para el robot (secreto CR_SESION) SIN que nadie vea la clave.
// Abre una ventana de Chrome aparte; Kevin inicia sesión ahí; el script guarda sólo las cookies de
// contentrewards.com / whop.com en base64, las copia al portapapeles, borra el archivo y abre la página de
// GitHub para crear el secreto. No imprime la sesión.
// Uso (en la PC, una vez):  npm i playwright-core  &&  node sesion-cr.mjs
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, exec } from 'node:child_process';

const navegador = await chromium.launch({ channel: 'chrome', headless: false, args: ['--start-maximized'] });
const ctx = await navegador.newContext({ viewport: null, locale: 'es-ES' });
const page = await ctx.newPage();
await page.goto('https://contentrewards.com/c/campaigns');
console.log('VENTANA_ABIERTA: inicia sesión en Content Rewards (con correo y código, no con Google).');

const limite = Date.now() + 20 * 60 * 1000;
let dentro = false;
while (Date.now() < limite) {
  try {
    if (page.url().includes('contentrewards.com/c/') &&
        await page.getByRole('button', { name: /Menú de cuenta|Account menu/i }).count()) { dentro = true; break; }
  } catch { /* la página está cambiando */ }
  await new Promise(r => setTimeout(r, 3000));
}
if (!dentro) { console.log('SIN_SESION: no se detectó el inicio de sesión en 20 min.'); await navegador.close(); process.exit(2); }
await new Promise(r => setTimeout(r, 4000));

const st = await ctx.storageState();
const dominio = /(^|\.)contentrewards\.com$|(^|\.)whop\.com$/;
const cookies = st.cookies.filter(c => dominio.test(c.domain.replace(/^\./, '')));
const origins = st.origins
  .filter(o => /contentrewards\.com|whop\.com/.test(o.origin))
  .map(o => ({ origin: o.origin, localStorage: o.localStorage.filter(x => !/posthog|intercom|query-cache|_ph_/i.test(x.name)) }));
await navegador.close();

const b64 = Buffer.from(JSON.stringify({ cookies, origins })).toString('base64');
const archivo = path.join(os.tmpdir(), `cr-${process.pid}.txt`);
fs.writeFileSync(archivo, b64);
execFileSync('powershell', ['-NoProfile', '-Command', `Get-Content -Raw -LiteralPath '${archivo}' | Set-Clipboard`]);
fs.unlinkSync(archivo);
console.log(`LISTO: sesión copiada al portapapeles (${cookies.length} cookies, ${b64.length} caracteres). Archivo borrado.`);
exec('start "" "https://github.com/ksflores-cmyk/granja-clips/settings/secrets/actions/new"');
