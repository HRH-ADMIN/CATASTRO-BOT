import io, sys, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9222")
    # Buscar específicamente la pestaña del contrato APT2 (con NumContrato)
    page = None
    for c in b.contexts:
        for pg in c.pages:
            try:
                u = pg.url
                if "APT2/Contrato/Nuevo" in u and "NumContrato" in u:
                    page = pg
                    break
            except Exception:
                continue
        if page:
            break
    if not page:
        print("NO encontré pestaña APT2 con NumContrato")
        sys.exit(1)
    page.bring_to_front()
    print(f"URL: {page.url}")
    # Asegurar tab CONTRATO
    page.evaluate(r"""() => {
        const links = [...document.querySelectorAll('a')];
        const pl = links.find(a => /^\s*CONTRATO\s*$/.test(a.innerText) || /contrato-tab/.test(a.href || ''));
        if (pl) pl.click();
    }""")
    time.sleep(1.5)
    page.evaluate("document.querySelector('#bC3')?.click();")
    time.sleep(0.6)
    info = page.evaluate(r"""() => {
        // Buscar inputs con email patterns
        const all = [...document.querySelectorAll('input')];
        const correo_like = all.filter(e => {
            const id = (e.id || '').toLowerCase();
            const name = (e.name || '').toLowerCase();
            const v = e.value || '';
            return /correo|email|profesional/.test(id) ||
                   /correo|email|profesional/.test(name) ||
                   v.includes('@');
        });
        return correo_like.map(e => ({
            id: e.id, name: e.name, value: e.value,
            readonly: e.readOnly, disabled: e.disabled,
            visible: e.offsetParent !== null
        }));
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=2))
