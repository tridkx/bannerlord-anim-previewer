"""Real Wudu cloth regression; requires its installed, baked race assets."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from baker.server import serve_background
from baker.shot import _launch
from playwright.sync_api import sync_playwright


def verify(mod='WuduRaceTest'):
    server, base = serve_background()
    try:
        with sync_playwright() as pw:
            browser = _launch(pw, 1200, 900, True)
            page = browser.new_page(viewport=dict(width=1200, height=900))
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base + f'?mod={mod}&skin=woman&anim=walk_forward_unarmed&frame=1&cloth=1')
            page.wait_for_function('window.__ready === true', timeout=120000)
            page.wait_for_function('window.__preview.state.texPending === 0', timeout=60000)
            result = page.evaluate('''() => {
                const p = window.__preview;
                p.state.playing = false;
                let finite = true, boundError = 0, displacement = 0, anchorError = 0;
                const start = performance.now();
                for (let f=0; f<60; f++) p.stepCloth(1/60);
                for (const n of p.state.scene.filter(n=>n.visible && n.kind === 'mod')) {
                    for (const c of n.cloth || []) {
                        if (!c.active) continue;
                        finite &&= c.position.every(Number.isFinite);
                        for (let i=0; i<c.reps.length; i++) {
                            const o=i*3;
                            const d=Math.hypot(...[0,1,2].map(k=>c.p[o+k]-c.target[o+k]));
                            displacement=Math.max(displacement,d);
                            boundError=Math.max(boundError,d-c.radius[i]);
                            if (!c.radius[i]) anchorError=Math.max(anchorError,d);
                        }
                    }
                }
                return {status:p.clothStatus().filter(c=>c.active), finite, boundError,
                    displacement, anchorError, elapsedMs:performance.now()-start};
            }''')
            expected = 5 if mod == 'WuduRaceTest' else len(result['status'])
            assert expected > 0, result
            assert len(result['status']) == expected, result
            if mod == 'WuduRaceTest':
                assert all(c['raceBody'] for c in result['status']), result
            assert result['finite'], result
            assert result['displacement'] > .0001, result
            assert result['boundError'] < 1e-5, result
            assert result['anchorError'] < 1e-6, result
            page.click('[data-tab="view"]')
            Path('_out').mkdir(exist_ok=True)
            page.screenshot(path=f'_out/{mod}_cloth.png')
            page.uncheck('#cloth-enabled')
            page.wait_for_function('!window.__preview.state.scene.some(n=>n.gpu.some(g=>g.clothActive))')
            page.check('#cloth-enabled')
            page.click('#cloth-reset')
            page.wait_for_function(f'window.__preview.clothStatus().filter(c=>c.active).length === {expected}')
            if mod == 'WuduRaceTest':
                page.click('[data-tab="equip"]')
                page.select_option('#skin-select', 'wdr_canglan_man')
                page.wait_for_function("document.querySelector('#loading').classList.contains('hidden') && window.__preview.clothStatus().filter(c=>c.active).length === 5")
            page.click('[data-tab="anim"]')
            page.get_by_text('run_forward_unarmed', exact=True).click()
            page.wait_for_function(f"window.__preview.state.animKey === 'run_forward_unarmed' && window.__preview.clothStatus().filter(c=>c.active).length === {expected}")
            assert not errors, errors
            assert page.locator('#err').count() == 0
            print(json.dumps(dict(**result, errors=errors), ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    verify(sys.argv[1] if len(sys.argv) > 1 else 'WuduRaceTest')
