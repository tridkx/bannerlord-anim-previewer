"""Installed Wudu race + equipment regression: python tests/browser_races.py."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from baker.server import serve_background
from baker.shot import _launch
from playwright.sync_api import sync_playwright


def verify():
    server, base = serve_background()
    try:
        with sync_playwright() as pw:
            browser = _launch(pw, 1200, 900, True)
            page = browser.new_page(viewport=dict(width=1200, height=900))
            errors, failures = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('response', lambda r: failures.append(r.url) if r.status >= 400 else None)
            mods = page.request.get(base + 'api/mods').json()
            assert any(m['name'] == 'WuduRaceTest' and m['hasRaces'] for m in mods)
            page.goto(base + '?mod=WuduRaceTest&skin=woman&anim=inventory_idle&frame=200&vanilla=0')
            page.wait_for_function('window.__ready === true', timeout=120000)
            page.wait_for_function('window.__preview.state.texPending === 0', timeout=60000)
            result = page.evaluate('''() => {
              const s = window.__preview.state;
              s.playing = false;
              return {skin:s.skinName, rig:s.rig.names, nodes:s.scene.filter(n=>n.visible).map(n=>({mesh:n.mesh,body:!!n.raceBody})),
                options:[...document.querySelector('#skin-select').options].map(o=>o.value)};
            }''')
            assert result['skin'] == 'wdr_canglan_woman', result
            assert result['rig'][0] == 'pelvis', result
            assert len(result['options']) == 2, result
            assert all(n['body'] for n in result['nodes']), result
            assert any(n['mesh'] == 'wdr_canglan_body' for n in result['nodes']), result
            page.screenshot(path='_out/WuduRaceTest_race.png')
            page.click('[data-tab="equip"]')
            page.select_option('#skin-select', 'wdr_canglan_man')
            page.wait_for_function("window.__preview.state.skinName === 'wdr_canglan_man' && document.querySelector('#loading').classList.contains('hidden')")
            assert page.evaluate("window.__preview.state.scene.filter(n=>n.raceBody).length") == len(result['nodes'])
            page.evaluate("window.__preview.state.frame = 450")
            page.screenshot(path='_out/WuduRaceTest_race_frame450.png')
            page.goto(base + '?mod=PitaoYingOutfits&skin=woman&anim=inventory_idle&frame=200')
            page.wait_for_function('window.__ready === true', timeout=120000)
            assert page.evaluate("window.__preview.state.scene.some(n=>n.visible && n.item)")
            assert page.evaluate("window.__preview.state.scene.some(n=>n.kind==='vanilla')")
            assert page.evaluate("window.__preview.state.skinName") == 'woman'
            assert not errors, errors
            assert not failures, failures
            print(json.dumps(dict(**result, errors=errors, failures=failures), ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    verify()
