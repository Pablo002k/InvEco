"""Trae precios de IOL y los guarda en data/precios.json.
Solo LEE precios. No usa ninguna función de compra o venta.
La clave llega por variables secretas: nunca se escribe acá."""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://api.iolinversiones.com"
TIPOS = ["acciones", "cedears", "etfs"]   # tipos de activo a traer
PLAZO = "T1"                    # T1 = 24hs, T0 = inmediato


def llamar(ruta, params=None, cuerpo=None, token=None):
    url = BASE + ruta + ("?" + urllib.parse.urlencode(params) if params else "")
    cab = {"Accept": "application/json", "User-Agent": "prueba-aula/1.0"}
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode()
        cab["Content-Type"] = "application/json"
    if token:
        cab["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=datos, headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detalle = json.load(e).get("error")
        except Exception:
            detalle = None
        raise RuntimeError(f"HTTP {e.code} en {ruta}: {detalle}")


def main():
    usuario, clave = os.environ.get("IOL_USUARIO"), os.environ.get("IOL_CLAVE")
    if not usuario or not clave:
        sys.exit("Faltan los secretos IOL_USUARIO e IOL_CLAVE en GitHub.")

    try:
        r = llamar("/auth/token", cuerpo={"grant_type": "password",
                                          "username": usuario, "password": clave})
        token = r["data"]["access_token"]
    except Exception as e:
        sys.exit(f"No se pudo iniciar sesión en IOL ({e}). "
                 "Puede ser usuario/clave incorrectos, la verificación en dos pasos "
                 "o un bloqueo de IOL a los servidores de GitHub.")
    print("Inicio de sesión: OK")

    tipos = llamar("/broker/assets/types", {"country": "AR"}, token=token)["data"]
    print("Tipos de activo disponibles:", ", ".join(t["id"] for t in tipos))
    disponibles = {t["id"] for t in tipos}

    precios, resumen = {}, {}
    for tipo in TIPOS:
        if tipo not in disponibles:
            print(f"Aviso: el tipo '{tipo}' no existe, se omite.")
            continue
        paneles = llamar("/broker/assets/panels",
                         {"country": "AR", "asset_type": tipo}, token=token)["data"]
        print(f"[{tipo}] paneles:", ", ".join(p["id"] for p in paneles))
        for p in paneles:
            time.sleep(0.5)
            try:
                filas = llamar("/broker/market-data/screener",
                               {"country": "AR", "asset_type": tipo,
                                "panel": p["id"], "term": PLAZO}, token=token)["data"]
            except Exception as e:
                print(f"  Panel {p['id']} falló: {e}")
                continue
            for f in filas:
                i, q, b = f["instrument"], f["quotes"], f.get("book") or {}
                precios[i["symbol"]] = {
                    "nombre": i["name"], "tipo": tipo, "moneda": i["currency"],
                    "ultimo": q["last"], "apertura": q["open"],
                    "cierre_anterior": q["close"], "maximo": q["high"],
                    "minimo": q["low"], "variacion_pct": q["change_percent"],
                    "volumen": q["volume"],
                    "compra": (b.get("bid") or {}).get("price"),
                    "venta": (b.get("ask") or {}).get("price"),
                }
                resumen[tipo] = resumen.get(tipo, 0) + 1

    if not precios:
        sys.exit("No llegó ningún precio: no se modifica el archivo.")

    salida = {"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "IOL", "plazo": PLAZO, "precios": precios}
    os.makedirs("data", exist_ok=True)
    with open("data/precios.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
    print("Listo. Activos guardados:", len(precios), resumen)
    print("Ejemplos:", ", ".join(list(precios)[:8]))


main()
