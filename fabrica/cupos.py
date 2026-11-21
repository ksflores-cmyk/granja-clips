"""Cuenta de peticiones a los modelos (estado/modelos-cuota.json): ventana de 60 s, día y tokens del día.

Imita a `publicar.Cuota`: un JSON chico, hora de Guatemala para el día, y si el archivo está roto se reinicia
sin romper la toma. Los críticos usan `hay_cupo`/`esperar_cupo` con los topes de `config/criticos.yml`; los ojos
sólo `registrar` (para que la cuenta del día sea real). Los críticos nunca tocan las últimas `reserva_ojos`
peticiones del día: son de los ojos.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .config import raiz_repo
from .pausa import ZONA

VENTANA_S = 60.0


class Cupos:
    def __init__(self, raiz=None, ahora=None):
        self.ruta = raiz_repo(raiz) / "estado" / "modelos-cuota.json"
        self.ahora = ahora or (lambda: datetime.now(timezone.utc))

    def _leer(self) -> dict:
        t = self.ahora()
        hoy = t.astimezone(ZONA).date().isoformat()
        datos: dict = {}
        if self.ruta.exists():
            try:
                bruto = json.loads(self.ruta.read_text(encoding="utf-8"))
                if isinstance(bruto, dict) and isinstance(bruto.get("proveedores"), dict):
                    datos = bruto["proveedores"]
            except (OSError, ValueError):
                datos = {}                         # roto: se reinicia
        limpio: dict = {}
        corte = t.timestamp() - VENTANA_S
        for nombre, p in datos.items():
            if not isinstance(p, dict) or not isinstance(nombre, str):
                continue
            try:
                ventana = [float(x) for x in p.get("ventana", []) if float(x) > corte]
                dia, n, tok = p.get("dia"), int(p.get("peticiones_dia", 0)), int(p.get("tokens_dia", 0))
            except (TypeError, ValueError):
                continue
            if dia != hoy:
                n, tok = 0, 0
            limpio[nombre] = {"ventana": ventana, "dia": hoy, "peticiones_dia": max(n, 0), "tokens_dia": max(tok, 0)}
        return limpio

    def _guardar(self, datos: dict) -> None:
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            self.ruta.write_text(json.dumps({"proveedores": datos}, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass                                  # la cuenta es una medida, nunca debe romper una toma

    def _de(self, datos: dict, proveedor: str) -> dict:
        hoy = self.ahora().astimezone(ZONA).date().isoformat()
        return datos.setdefault(proveedor, {"ventana": [], "dia": hoy, "peticiones_dia": 0, "tokens_dia": 0})

    def registrar(self, proveedor: str, tokens: int = 0) -> None:
        """Una petición enviada (cuenta aunque falle: gasta cupo)."""
        datos = self._leer()
        p = self._de(datos, proveedor)
        p["ventana"].append(self.ahora().timestamp())
        p["peticiones_dia"] += 1
        p["tokens_dia"] += max(int(tokens), 0)
        self._guardar(datos)

    def sumar_tokens(self, proveedor: str, tokens: int) -> None:
        if tokens and tokens > 0:
            datos = self._leer()
            self._de(datos, proveedor)["tokens_dia"] += int(tokens)
            self._guardar(datos)

    def uso(self, proveedor: str) -> dict:
        p = self._leer().get(proveedor) or {"ventana": [], "peticiones_dia": 0, "tokens_dia": 0}
        return {"ventana": len(p["ventana"]), "dia": p["peticiones_dia"], "tokens_dia": p["tokens_dia"]}

    def hay_cupo(self, proveedor: str, n: int = 1, *, rpm=None, rpd=None, reserva: int = 0) -> tuple[bool, str, float]:
        """(hay, motivo, espera_s). `espera_s` > 0 sólo si lo que falta es la ventana de un minuto (vale esperar).

        El tope del día descuenta `reserva` (las peticiones que se guardan para los ojos)."""
        datos = self._leer()
        p = datos.get(proveedor) or {"ventana": [], "peticiones_dia": 0}
        if rpd is not None and p["peticiones_dia"] + n > int(rpd) - int(reserva):
            return False, (f"cupo del día agotado para {proveedor} ({p['peticiones_dia']} de {rpd}, "
                           f"con {reserva} reservadas para los ojos)"), 0.0
        if rpm is not None and len(p["ventana"]) + n > int(rpm):
            ventana = sorted(p["ventana"])
            sobran = len(ventana) + n - int(rpm)           # cuántas deben salir de la ventana
            if sobran > len(ventana):
                return False, f"la petición no cabe en {rpm} por minuto", 0.0
            espera = max(0.0, ventana[sobran - 1] + VENTANA_S - self.ahora().timestamp()) + 0.5
            return False, f"ventana de un minuto llena para {proveedor} ({len(ventana)} de {rpm})", espera
        return True, "", 0.0

    def esperar_cupo(self, proveedor: str, n: int = 1, *, rpm=None, rpd=None, reserva: int = 0,
                     espera=time.sleep, espera_max_s: float = 30.0) -> tuple[bool, str]:
        """Como `hay_cupo`, pero si sólo falta la ventana de un minuto espera (hasta `espera_max_s` en total).
        La espera es inyectable: las pruebas no duermen."""
        gastado = 0.0
        while True:
            ok, motivo, falta = self.hay_cupo(proveedor, n, rpm=rpm, rpd=rpd, reserva=reserva)
            if ok:
                return True, ""
            if falta <= 0 or gastado + falta > espera_max_s:
                return False, motivo + ("" if falta <= 0 else f"; esperar {falta:.0f} s pasa del máximo de {espera_max_s:g} s")
            espera(falta)
            gastado += falta


def tokens_de_gemini(texto_http: str) -> int:
    """`usageMetadata.totalTokenCount` de una respuesta de Gemini (0 si no viene)."""
    try:
        d = json.loads(texto_http)
        n = (d.get("usageMetadata") or {}).get("totalTokenCount", 0)
        return int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
    except (ValueError, TypeError, AttributeError):
        return 0
