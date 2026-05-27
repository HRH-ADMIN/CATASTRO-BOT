import io, sys, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9222")
    page = None
    for c in b.contexts:
        for pg in c.pages:
            if "APT2/Contrato/Nuevo" in pg.url and "NumContrato" in pg.url:
                page = pg
                break
        if page:
            break
    page.bring_to_front()
    page.evaluate(r"""() => {
        const links = [...document.querySelectorAll('a')];
        const pl = links.find(a => /^\s*CONTRATO\s*$/.test(a.innerText));
        if (pl) pl.click();
    }""")
    time.sleep(1.2)
    page.evaluate("document.querySelector('#bC4')?.click();")
    time.sleep(0.6)
    info = page.evaluate(r"""() => {
        const sel = document.querySelector('#dllprotocolo');
        if (!sel) return {error: 'no dropdown'};
        return {
            current_value: sel.value,
            opt_count: sel.options.length,
            all_options: [...sel.options].map(o => ({v: o.value, t: o.text})),
        };
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=2))
