"""Real browser regression; run: python tests/browser_multi_tpac.py [ModName].

Requires an installed, baked multi-TPAC mod with multiple human equipment sets.
Uses the same Python Playwright dependency as the shot command.
"""
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baker.server import serve_background
from baker.shot import _launch
from playwright.sync_api import sync_playwright


def verify(mod='ShengxiuCollection'):
    server, base = serve_background()
    try:
        with sync_playwright() as pw:
            browser = _launch(pw, 1280, 900, True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors, failures, bakes = [], [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('response', lambda response: failures.append(response.url)
                    if response.status >= 400 else None)
            page.on('request', lambda request: bakes.append(request.url)
                    if '/api/bake?' in request.url else None)
            first = True

            def old_manifest(route):
                nonlocal first
                response = route.fetch()
                manifest = response.json()
                if first:
                    first = False
                    manifest.pop('packageVersion', None)
                route.fulfill(response=response, json=manifest)

            page.route(f'**/mods/{quote(mod)}/manifest.json', old_manifest)
            page.goto(base + f'?mod={quote(mod)}&anim=walk_forward_unarmed&frame=60',
                      wait_until='domcontentloaded')
            page.wait_for_function('() => window.__ready === true', timeout=120000)
            assert len(bakes) == 1, bakes
            result = page.evaluate('''async () => {
                const p = window.__preview;
                p.state.playing = false;
                const results = [];
                for (const set of p.sets()) {
                    await p.wear(set.members);
                    const nodes = p.state.scene.filter(n => n.visible && n.kind === 'mod');
                    results.push({label:set.label, expected:set.members,
                        actual:[...new Set(nodes.map(n => n.item?.id))].sort(), nodes:nodes.length});
                }
                return {packs:p.state.manifest.sourcePacks, sets:results};
            }''')
            assert len(result['packs']) > 1, result
            assert len(result['sets']) > 1, result
            for outfit in result['sets']:
                assert outfit['actual'] == sorted(outfit['expected']), outfit
                assert outfit['nodes'] > 0, outfit
            page.wait_for_function('() => window.__preview.state.texPending === 0', timeout=60000)
            assert not errors, errors
            assert not failures, failures
            print(json.dumps(dict(mod=mod, **result, automaticMigrationBakes=len(bakes),
                                  errors=errors, failures=failures), ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    verify(sys.argv[1] if len(sys.argv) > 1 else 'ShengxiuCollection')
