const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const server = spawn('python', ['cli/mbpreview.py', 'serve', '--port', '8785', '--no-browser'],
  {cwd: root, stdio: 'ignore', windowsHide: true});
(async () => {
  let browser;
  try {
    for (let i=0; i<40; i++) {
      try { if ((await fetch('http://127.0.0.1:8785/api/mods')).ok) break; } catch {}
      await new Promise(r => setTimeout(r, 250));
    }
    browser = await chromium.launch({channel: 'msedge', headless: true,
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']});
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:8785/?mod=TianxiangT5&anim=walk_forward_unarmed&frame=1',
      {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => window.__ready, {timeout:120000});
    const initial = await page.evaluate(() => {
      const p = window.__preview;
      p.state.playing = false;
      return {status:p.clothStatus(), visible:p.visibleMeshes};
    });
    assert.ok(initial.visible > 0);
    assert.ok(initial.status.some(c => c.active), 'real TPAC activates cloth');
    const result = await page.evaluate(() => {
      const p = window.__preview;
      let max = 0, maxBound = 0, finite = true;
      const start = performance.now();
      for (let i=0; i<60; i++) p.stepCloth(1/60);
      const elapsed = performance.now() - start;
      for (const n of p.state.scene.filter(n => n.visible)) for (const c of n.cloth || []) {
        if (!c.active) continue;
        finite &&= c.position.every(Number.isFinite);
        for (let i=0; i<c.reps.length; i++) {
          const o=i*3;
          const d=Math.hypot(...[0,1,2].map(k => c.p[o+k]-c.target[o+k]));
          max=Math.max(max,d); maxBound=Math.max(maxBound,d-c.radius[i]);
        }
      }
      return {elapsed, max, maxBound, finite, status:p.clothStatus()};
    });
    assert.ok(result.finite);
    assert.ok(result.max > .0001, 'real animated cloth moves');
    assert.ok(result.maxBound < .00001, 'real cloth stays within painted distance');
    await page.waitForTimeout(650);
    await page.click('[data-tab="view"]');
    fs.mkdirSync(path.join(root,'_out'),{recursive:true});
    await page.screenshot({path:path.join(root,'_out','cloth-preview.png')});
    await page.uncheck('#cloth-enabled');
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => window.__preview.clothStatus().some(c=>c.active)), false);
    assert.equal(await page.evaluate(() => window.__preview.state.scene.some(n=>n.gpu.some(g=>g.clothActive))), false);
    await page.check('#cloth-enabled');
    await page.click('#cloth-reset');
    await page.waitForTimeout(100);
    assert.ok(await page.evaluate(() => window.__preview.clothStatus().some(c=>c.active)));
    assert.deepEqual(errors, []);
    assert.equal(await page.locator('#err').count(), 0, 'render loop has no caught fatal error');
    assert.match(await page.innerText('#cloth-status'), /模拟 3 个部件/);
    console.log(JSON.stringify({visible:initial.visible, active:initial.status.filter(c=>c.active),
      elapsedMs:result.elapsed, maxDistance:result.max, boundError:result.maxBound,
      finite:result.finite, errors},null,2));
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
